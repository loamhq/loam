// The calm editor: one Markdown doc, its notes in the margin, three ways of
// working (Editing, Suggesting, Viewing), formatting that appears only when it
// is wanted, History and authors behind ⋯.
//
// It is a standalone component. Inside Loam it is mounted by page/main.js as
// Files' viewer for Markdown; in step 2 the same component is the outsider's
// page. Everything it needs from its host comes in through `createDocEditor`'s
// options: the operations (`api`, the plugin server's routes), faces, links,
// embeds, the breadcrumb, and whatever the host puts in the header or bar
// (Loam's dictation mic, read aloud).

import {
  EditorState, EditorView, EditorSelection, ChangeSet, Compartment, Transaction, Annotation, Prec,
  keymap, history, historyKeymap, defaultKeymap, drawSelection, indentMore, indentLess, syntaxTree,
} from '../vendor/codemirror.js'
import { h, icon, rel, stamp, put } from './dom.js'
import { changes as diffChanges, posMap, editorText, suggestionShape } from './textdiff.js'
import {
  markdownLanguage, inline, blocks, notesField, setNotes, authorsField, setAuthors,
  modeField, setMode, suggestBase, setSuggestBase, liveSuggest, focusField, setFocused,
} from './live.js'
import { INLINE, BLOCKS, active, toggleInline, setLink, linkAt, setBlock, blockOf } from './format.js'

export const AUTOSAVE_MS = 1200
export const WATCH_MS = 1500
const remote = Annotation.define()
const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')
const MOD = MAC ? '⌘' : 'Ctrl'

const MODES = [
  { id: 'edit', label: 'Editing', icon: 'pencil', hint: 'Change the text directly' },
  { id: 'suggest', label: 'Suggesting', icon: 'suggest', hint: 'Your edits become suggestions' },
  { id: 'view', label: 'Viewing', icon: 'eye', hint: 'Read; notes step out of the way' },
]

export function lineSeparatorOf(text) {
  const crlf = (text.match(/\r\n/g) || []).length
  const lf = (text.match(/\n/g) || []).length
  return crlf && crlf === lf ? '\r\n' : '\n'
}

function defaultFace(author, size) {
  const name = author?.name || '?'
  return h('span', { class: 'dc-face', style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.48)}px` }, text: name.charAt(0).toUpperCase() })
}

// Where the doc's own words begin: after its frontmatter, so the caret does
// not start inside the Properties block (which would show it as source).
function bodyStart(text) {
  const m = /^---\r?\n(?:[\s\S]*?\r?\n)?---[ \t]*(\r?\n|$)/.exec(text)
  if (!m) return 0
  let at = m[0].length
  while (text[at] === '\n' || text[at] === '\r') at++
  return Math.min(at, text.length)
}

export function createDocEditor(opts) {
  const {
    api, face = defaultFace, embed = null, openLink = null, settings = null,
    fileMenu = null, onText = null, onState = null,
  } = opts
  // The editor's working text is "editor space" (textdiff.js, posMap): a file
  // whose lines all end in CRLF is held with one position per line break.
  // What is sent is always the file's own text (sliceDoc), and every position
  // to or from the server goes through `st.pm`.
  const CRLF = lineSeparatorOf(opts.doc.text) === '\r\n'
  const ed = (t) => editorText(t, CRLF)
  const raw = (t) => (CRLF ? t.replace(/\r?\n/g, '\r\n') : t)
  const st = {
    server: { text: opts.doc.text, revision: opts.doc.revision },
    pm: posMap(opts.doc.text, CRLF),
    mapBase: ed(opts.doc.text),
    sinceBase: ChangeSet.empty(ed(opts.doc.text).length),
    notes: [], me: opts.doc.me, can: opts.doc.can || {}, id: opts.doc.id, resolved: 0,
    // Every doc opens in Editing; a mode lasts while that doc is open and is
    // never remembered (a remembered Suggesting made a doc never opened
    // before grow a sidecar at its first keystroke).
    mode: 'edit',
    focus: null, peek: null, compose: null,
    drawer: null, history: null, authors: Boolean(settings?.get('authors', false)), authorSpans: null,
    menu: null, saving: false, dirty: false, again: false, word: '', error: '',
    phone: Boolean(opts.phone), reattach: null, toast: null, sheet: null, bmenu: null, link: null,
    destroyed: false, editing: null, openReplies: new Set(), preview: null, sheetFor: null,
  }
  if (!opts.doc.can?.edit && st.mode !== 'view') st.mode = opts.doc.can?.suggest ? 'suggest' : 'view'
  let saveTimer = null, suggestTimer = null, layoutQueued = false
  let queue = Promise.resolve()
  const people = { ...(opts.people || {}) }
  const historyC = new Compartment(), editableC = new Compartment()

  // -- the DOM ---------------------------------------------------------------------

  const root = h('div', { class: 'dc' })
  const head = h('div', { class: 'dc-head' })
  const take = h('div', { class: 'dc-take' })
  const player = h('div', { class: 'dc-player' })
  const scroll = h('div', { class: 'dc-scroll' })
  const page = h('div', { class: 'dc-page' })
  const col = h('div', { class: 'dc-col' })
  const legend = h('div', { class: 'dc-legend', 'aria-hidden': 'true' })
  const gutter = h('div', { class: 'dc-gutter' })
  const plus = h('button', { class: 'dc-plus', type: 'button', title: 'Add a block  ·  type / on an empty line', 'aria-label': 'Add a block' }, icon('plus', 17))
  const margin = h('div', { class: 'dc-margin', role: 'complementary', 'aria-label': 'Comments and suggestions' })
  const selbar = h('div', { class: 'dc-selbar', role: 'toolbar', 'aria-label': 'Formatting and notes' })
  const bmenu = h('div', { class: 'dc-bmenu', role: 'menu' })
  const qpop = h('div', { class: 'dc-qpop' })
  const kbar = h('div', { class: 'dc-kbar', role: 'toolbar', 'aria-label': 'Formatting' })
  const vbar = h('div', { class: 'dc-vbar' })
  const drawer = h('aside', { class: 'dc-drawer' })
  const sheetScrim = h('div', { class: 'dc-scrim' })
  const sheet = h('div', { class: 'dc-sheet', role: 'dialog' })
  const toast = h('div', { class: 'dc-toast', role: 'status' })
  const banner = h('div', { class: 'dc-banner', role: 'status' })
  col.append(legend, gutter, plus)
  page.append(col, margin)
  scroll.append(page)
  root.append(head, take, player, banner, scroll, kbar, vbar, selbar, bmenu, qpop, drawer, sheetScrim, sheet, toast)

  // -- placing things beside the text ----------------------------------------------
  //
  // Loam zooms its whole page (Settings, Size: `body { zoom }`), so a rectangle
  // read from the DOM, and CodeMirror's own line heights, are in screen pixels,
  // while a `top` or `left` set on an element inside the page is in the page's
  // own pixels. At 90% every placement drifted by a tenth of its distance (29 px
  // by the first paragraph, 200 by the end of a long doc). Everything placed
  // goes through these.
  function zoom() {
    const w = root.offsetWidth
    const z = w ? root.getBoundingClientRect().width / w : 1
    return z > 0.2 && z < 5 ? z : 1
  }
  // A screen rectangle, in the page's pixels relative to `el` (the editor's
  // root unless named).
  function local(rect, el = root) {
    const z = zoom(), box = el.getBoundingClientRect()
    return { left: (rect.left - box.left) / z, right: (rect.right - box.left) / z, top: (rect.top - box.top) / z, bottom: (rect.bottom - box.top) / z }
  }
  function rootSize() {
    return { width: root.offsetWidth, height: root.offsetHeight }
  }
  // Where the line holding `pos` begins, in the page's pixels from the top of
  // the editor's content (what the gutter, the "+" and the margin count from).
  function lineTop(pos) {
    return view.lineBlockAt(pos).top / zoom()
  }

  // -- CodeMirror --------------------------------------------------------------------

  const keys = [
    { key: 'Mod-b', run: v => (canFormat() ? (toggleInline(v, 'bold'), true) : false) },
    { key: 'Mod-i', run: v => (canFormat() ? (toggleInline(v, 'italic'), true) : false) },
    { key: 'Mod-Shift-x', run: v => (canFormat() ? (toggleInline(v, 'strike'), true) : false) },
    { key: 'Mod-e', run: v => (canFormat() ? (toggleInline(v, 'code'), true) : false) },
    { key: 'Mod-k', run: () => (canFormat() ? (openLink_(), true) : false) },
    { key: 'Mod-Alt-m', run: () => (startNote('comment'), true) },
    { key: 'Mod-s', run: () => { flush(); return true } },
    { key: 'Ctrl-Alt-0', run: v => (canFormat() ? (setBlock(v, 'p'), true) : false) },
    { key: 'Ctrl-Alt-1', run: v => (canFormat() ? (setBlock(v, 'h1'), true) : false) },
    { key: 'Ctrl-Alt-2', run: v => (canFormat() ? (setBlock(v, 'h2'), true) : false) },
    { key: 'Ctrl-Alt-3', run: v => (canFormat() ? (setBlock(v, 'h3'), true) : false) },
    { key: 'Tab', run: v => (inList(v) ? indentMore(v) : false) },
    { key: 'Shift-Tab', run: v => (inList(v) ? indentLess(v) : false) },
    { key: 'Escape', run: () => escape() },
  ]

  function inList(v) {
    const line = v.state.doc.lineAt(v.state.selection.main.head)
    return /^\s*([-*+]|\d+[.)])\s/.test(line.text)
  }

  // A list continues on Enter, and an empty item ends it: the Markdown habit.
  // Ending it leaves a blank line, so on disk what follows is a paragraph of
  // its own and not still inside the last item (or quote); an empty item
  // that is indented steps out one level instead.
  function continueList(v) {
    const { state } = v
    const r = state.selection.main
    if (!r.empty) return false
    const line = state.doc.lineAt(r.head)
    const m = /^(\s*)([-*+] \[[ xX]\] |[-*+] |(\d+)([.)]) |> ?)/.exec(line.text)
    if (!m || r.head < line.from + m[0].length) return false
    if (line.text.trim() === m[0].trim()) {
      if (m[1] && !m[2].startsWith('>')) return indentLess(v)
      const sep = state.lineBreak
      const prevBlank = line.number > 1 && !state.doc.line(line.number - 1).text.trim()
      const insert = prevBlank ? '' : sep
      v.dispatch({ changes: { from: line.from, to: line.to, insert }, selection: { anchor: line.from + insert.length }, userEvent: 'input', scrollIntoView: true })
      return true
    }
    if (m[2].startsWith('>') && !m[2].endsWith(' ')) return false
    let next = m[2]
    if (m[3]) next = `${Number(m[3]) + 1}${m[4]} `
    else if (/\[[xX]\]/.test(next)) next = next.replace(/\[[xX]\]/, '[ ]')
    const sep = state.lineBreak
    v.dispatch({ changes: { from: r.head, insert: sep + m[1] + next }, selection: { anchor: r.head + sep.length + m[1].length + next.length }, userEvent: 'input', scrollIntoView: true })
    return true
  }

  // Where Enter keeps its own meaning: code, tables, the frontmatter.
  function inRawBlock(state, pos) {
    const fm = /^---\r?\n(?:[\s\S]*?\r?\n)?---[ \t]*(?=\r?\n|$)/.exec(state.doc.sliceString(0, Math.min(state.doc.length, 20000)))
    if (fm && pos <= fm[0].length) return true
    for (let n = syntaxTree(state).resolveInner(pos, -1); n; n = n.parent) {
      if (/^(FencedCode|CodeBlock|Table|HTMLBlock|CommentBlock)$/.test(n.name)) return true
    }
    return false
  }

  // In a paragraph or a heading, Enter starts a new paragraph: a blank line on
  // disk, as Markdown needs (one line break is the same paragraph). Shift-Enter
  // is one line break. Lists and quotes continue (above); code blocks, tables
  // and the frontmatter keep CodeMirror's own Enter.
  function newParagraph(v) {
    if (!canFormat()) return false
    const { state } = v
    const r = state.selection.main
    const line = state.doc.lineAt(r.from)
    if (!line.text.trim() || inRawBlock(state, r.from)) return false
    const sep = state.lineBreak
    // Splitting a line: the words after the caret start the new paragraph,
    // without the spaces that were before them.
    let to = r.to
    while (to < state.doc.lineAt(r.to).to && /[ \t]/.test(state.sliceDoc(to, to + 1))) to++
    let from = r.from
    while (from > line.from && /[ \t]/.test(state.sliceDoc(from - 1, from))) from--
    const insert = sep + sep
    v.dispatch({ changes: { from, to, insert }, selection: { anchor: from + insert.length }, userEvent: 'input', scrollIntoView: true })
    return true
  }

  function lineBreak(v) {
    if (!canFormat()) return false
    v.dispatch(v.state.replaceSelection(v.state.lineBreak), { userEvent: 'input', scrollIntoView: true })
    return true
  }

  // Typed shortcuts that Markdown alone does not give: "[] " (or "[ ] ") at
  // the start of a line is a checklist item, and a third backtick opening a
  // code block closes it too, so the rest of the doc does not become code.
  function typedShortcut(v, from, to, text) {
    if (!canFormat() || from !== to) return false
    const { state } = v
    const line = state.doc.lineAt(from)
    const before = state.sliceDoc(line.from, from)
    const after = state.sliceDoc(to, line.to)
    if (text === ' ' && !after.trim()) {
      const m = /^(\s*)(?:[-*+] )?\[( |x|X)?\]$/.exec(before)
      if (m && !inRawBlock(state, from)) {
        const mark = `- [${m[2] && m[2] !== ' ' ? 'x' : ' '}] `
        const at = line.from + m[1].length
        v.dispatch({ changes: { from: at, to, insert: mark }, selection: { anchor: at + mark.length }, userEvent: 'input.type' })
        return true
      }
    }
    if (text === '`' && /^\s*``$/.test(before) && !after.trim()) {
      const indent = /^\s*/.exec(before)[0]
      const nextLine = line.number < state.doc.lines ? state.doc.line(line.number + 1).text : ''
      const inCode = inRawBlock(state, line.from)
      if (!inCode && !/^\s*```/.test(nextLine)) {
        const sep = state.lineBreak
        v.dispatch({ changes: { from, to, insert: '`' + sep + indent + '```' }, selection: { anchor: from + 1 }, userEvent: 'input.type' })
        return true
      }
    }
    return false
  }

  const view = new EditorView({
    parent: col,
    state: EditorState.create({
      doc: opts.doc.text,
      selection: { anchor: bodyStart(opts.doc.text) },
      extensions: [
        EditorState.lineSeparator.of(lineSeparatorOf(opts.doc.text)),
        markdownLanguage,
        modeField.init(() => st.mode),
        focusField,
        historyC.of(history()),
        editableC.of(editableExt()),
        drawSelection(),
        EditorView.lineWrapping,
        Prec.high(keymap.of([...keys, { key: 'Enter', run: (v) => continueList(v) || newParagraph(v) }, { key: 'Shift-Enter', run: lineBreak }])),
        EditorView.inputHandler.of(typedShortcut),
        // Room below the caret while typing at the end, and above it.
        EditorView.scrollMargins.of(() => ({ top: 24, bottom: st.phone ? 132 : 96 })),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        inline,
        blocks(embed),
        notesField,
        authorsField,
        suggestBase,
        liveSuggest((a, b) => diffChanges(a, b)),
        EditorView.contentAttributes.of({ spellcheck: 'true', autocorrect: 'on', autocapitalize: 'sentences', 'aria-label': 'Document' }),
        EditorView.updateListener.of(onUpdate),
        EditorView.domEventHandlers({
          mousedown: onEditorMouseDown,
          click: onEditorClick,
          mouseover: onEditorOver,
          mouseout: onEditorOut,
          keyup: () => { if (!st.phone) queueSelbar() },
          mouseup: () => { if (!st.phone) setTimeout(queueSelbar, 0) },
          focus: () => { view.dispatch({ effects: setFocused.of(true), annotations: remote.of(true) }); renderBars() },
          blur: () => setTimeout(() => {
            if (st.destroyed) return
            if (!view.hasFocus) view.dispatch({ effects: setFocused.of(false), annotations: remote.of(true) })
            // Focus went elsewhere (the margin, the header): the selection's
            // popup goes with it, unless it is the link field being typed in.
            if (!view.hasFocus && !st.link) hideSelbar()
            renderBars()
            if (st.mode === 'edit' && st.dirty) save()
          }, 120),
          input: onBeforeSlash,
        }),
      ],
    }),
  })

  function editableExt() {
    const writable = st.mode === 'view' ? false : st.mode === 'suggest' ? Boolean(st.can.suggest) : Boolean(st.can.edit)
    return [EditorView.editable.of(writable), EditorState.readOnly.of(!writable)]
  }

  function canFormat() {
    return (st.mode === 'edit' && st.can.edit) || (st.mode === 'suggest' && st.can.suggest)
  }

  // -- the server's answers ----------------------------------------------------------

  function people_(author) {
    if (!author) return { name: 'Someone', kind: 'owner' }
    return { ...author, ...(people[author.id] || {}) }
  }

  // Every answer carries the doc's text as stored. What the person typed since
  // the text the editor's changes are counted from is kept on top of it, and
  // the notes' passages are mapped into the editor's text the same way.
  function applyAnswer(ans) {
    if (st.destroyed || !ans) return
    const T = ans.text
    const ET = ed(T)
    let userOver
    try {
      const serverCS = ChangeSet.of(diffChanges(st.mapBase, ET), st.mapBase.length, '\n')
      userOver = st.sinceBase.map(serverCS, true)
      const forEditor = serverCS.map(st.sinceBase)
      if (!forEditor.empty) {
        const keep = scrollAnchor()
        view.dispatch({ changes: forEditor, annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
        if (keep) holdScroll(keep, forEditor)
      }
    } catch (err) {
      // The bookkeeping lost track (it never should): keep what is on the
      // screen as edits over the stored text, rather than lose either.
      console.error('doc-comments: rebasing on the stored text', err)
      const now = view.state.doc.toString()
      userOver = ChangeSet.of(diffChanges(ET, now), ET.length, '\n')
    }
    // Suggesting draws what is typed over the text as stored. When the stored
    // text moves (an accept, a save from elsewhere, an outside edit), that is
    // the base from now on, so nothing already in the text is suggested again.
    if (view.state.field(suggestBase, false) != null) {
      view.dispatch({ effects: setSuggestBase.of(ET), annotations: remote.of(true) })
    }
    st.server = { text: T, revision: ans.revision }
    st.pm = posMap(T, CRLF)
    st.mapBase = ET
    st.sinceBase = userOver
    st.id = ans.id
    st.me = ans.me || st.me
    st.can = ans.can || st.can
    st.resolved = ans.resolved || 0
    const map = (p) => (p == null ? p : userOver.mapPos(p, 1))
    st.notes = (ans.notes || []).map(n => {
      const w = n.where || {}
      const from = w.start == null ? null : map(st.pm.toEditor(w.start))
      const to = w.end == null ? null : userOver.mapPos(st.pm.toEditor(w.end), -1)
      return { ...n, from, to }
    })
    for (const n of st.notes) for (const a of [n.author, ...(n.replies || []).map(r => r.author)]) if (a?.id && !people[a.id]) people[a.id] = a
    if (st.focus && !st.notes.some(n => n.id === st.focus && n.state === 'open')) st.focus = null
    st.dirty = !st.sinceBase.empty && st.mode === 'edit'
    pushNotes()
    if (st.authors) loadAuthors()
    render()
  }

  // A change that lands above what the person is reading (someone else's
  // save, an edit on disk) must not move what they are reading: the first line
  // in view stays where it was on the screen.
  function scrollAnchor() {
    if (scroll.scrollTop <= 0) return null
    const y = scroll.getBoundingClientRect().top - view.documentTop
    if (y <= 0) return null
    const block = view.lineBlockAtHeight(y)
    return { pos: block.from, y: view.documentTop + block.top }
  }

  function holdScroll(keep, cs) {
    if (cs.touchesRange(0, keep.pos) === false) return
    const pos = cs.mapPos(keep.pos, 1)
    view.requestMeasure({
      read: () => view.documentTop + view.lineBlockAt(Math.min(pos, view.state.doc.length)).top,
      write: (y) => { if (Math.abs(y - keep.y) > 0.5) scroll.scrollTop += (y - keep.y) / zoom() },
    })
  }

  // A fresh read, quietly: with nothing unsaved the stored text is simply taken
  // in (the caret and the passages map through it); with unsaved edits they
  // are saved now, and the save merges them with what changed.
  function reloadQuietly() {
    if (st.destroyed) return Promise.resolve()
    if (st.mode === 'edit' && !st.sinceBase.empty) return save()
    return run(async () => applyAnswer(await api.get())).catch(fail)
  }

  // Changes made on disk (a persona, another app) are noticed while the doc is
  // open: its revision is asked every so often while the page is visible, and
  // a new one is read in. Nothing is asked while a call is on its way.
  let watchTimer = null, watching = false
  function watch() {
    clearTimeout(watchTimer)
    if (st.destroyed) return
    watchTimer = setTimeout(async () => {
      const visible = typeof document === 'undefined' || document.visibilityState !== 'hidden'
      if (visible && root.isConnected && !st.saving && !watching && api.revision) {
        watching = true
        try {
          const ans = await api.revision()
          if (ans?.revision && ans.revision !== st.server.revision && !st.saving) await reloadQuietly()
        } catch { /* the next look tries again */ }
        watching = false
      }
      watch()
    }, WATCH_MS)
  }

  function pushNotes() {
    const list = []
    for (const n of st.notes) {
      if (n.state !== 'open' || n.from == null) continue
      list.push({ id: n.id, kind: n.kind, from: n.from, to: n.to, replacement: n.replacement, focus: st.focus === n.id, peek: st.peek === n.id, changed: n.where?.state === 'changed' })
    }
    if (st.compose) list.push({ id: '__new', kind: 'comment', from: st.compose.from, to: st.compose.to, focus: true })
    view.dispatch({ effects: setNotes.of(list), annotations: remote.of(true) })
  }

  function currentNoteRanges() {
    return view.state.field(notesField).notes
  }

  function run(fn) {
    // One call to the server at a time, in order: each answer is the base the
    // next is mapped from.
    const p = queue.then(fn, fn)
    queue = p.catch(() => {})
    return p
  }

  // What a person is told when something fails: the server's own words
  // (they are written for people), and never a library's. Anything else is a
  // fault of the page's: it is logged, and the doc is read again.
  function fail(err) {
    const ours = typeof err?.status === 'number'
    const msg = ours ? (err.message || 'That did not work.') : 'Something went wrong here, so the doc has been read again.'
    if (!ours) {
      console.error('doc-comments:', err)
      setTimeout(() => { if (!st.destroyed) reloadQuietly() }, 0)
    }
    st.error = msg
    showToast(msg, null, true)
    render()
  }

  async function call(fn) {
    try {
      const ans = await run(async () => {
        await flushNow()
        return fn()
      })
      applyAnswer(ans)
      return ans
    } catch (err) {
      fail(err)
      return null
    }
  }

  // -- saving --------------------------------------------------------------------------

  function onUpdate(u) {
    if (u.docChanged && !u.transactions.some(t => t.annotation(remote))) {
      for (const t of u.transactions) if (t.docChanged) st.sinceBase = st.sinceBase.compose(t.changes)
      if (st.mode === 'edit') {
        st.dirty = true
        st.word = ''
        scheduleSave()
      } else if (st.mode === 'suggest') {
        scheduleSuggest()
      }
      onText?.(view.state.sliceDoc())
      closeBmenuIfStale()
    }
    if (u.docChanged || u.geometryChanged || u.viewportChanged || u.transactions.some(t => t.effects.length)) queueLayout()
    if (u.selectionSet && !st.phone) queueSelbar()
    if (u.selectionSet || u.focusChanged) { renderPlus(); if (st.phone) renderBars() }
  }

  function scheduleSave(ms = AUTOSAVE_MS) {
    clearTimeout(saveTimer)
    saveTimer = setTimeout(() => save(), ms)
    renderSaveWord()
  }

  function save() {
    clearTimeout(saveTimer)
    if (st.mode !== 'edit' || st.sinceBase.empty) return Promise.resolve()
    return run(saveNow).catch(fail)
  }

  async function saveNow() {
    if (st.sinceBase.empty || st.destroyed) return
    const text = view.state.sliceDoc()
    const body = { base_revision: st.server.revision, base_text: st.server.text, text }
    st.saving = true
    renderSaveWord()
    // From here, the editor's changes are counted from what is being sent.
    st.mapBase = view.state.doc.toString()
    st.sinceBase = ChangeSet.empty(view.state.doc.length)
    try {
      const ans = await api.save(body)
      st.saving = false
      st.error = ''
      st.word = ans.conflicts ? 'Saved · merged' : ans.merged_with_others ? 'Saved · merged' : 'Saved'
      applyAnswer(ans)
      if (ans.conflicts) {
        showToast(ans.conflicts === 1
          ? 'Someone changed the same words just before you. Theirs stands; yours is a suggestion.'
          : `Someone changed ${ans.conflicts} of the same passages just before you. Theirs stand; yours are suggestions.`)
      }
      if (!st.sinceBase.empty) scheduleSave()
    } catch (err) {
      st.saving = false
      // Put the unsent change back on top: nothing typed is lost.
      st.mapBase = ed(st.server.text)
      st.sinceBase = ChangeSet.of(diffChanges(st.mapBase, view.state.doc.toString()), st.mapBase.length, '\n')
      st.word = 'Couldn’t save'
      st.error = err?.message || String(err)
      renderSaveWord()
      clearTimeout(saveTimer)
      saveTimer = setTimeout(() => save(), 8000)
      throw err
    }
  }

  async function flushNow() {
    clearTimeout(saveTimer)
    if (st.mode === 'edit' && !st.sinceBase.empty) await saveNow()
    if (st.mode === 'suggest') await suggestNow()
  }

  function flush() {
    return run(flushNow).catch(fail)
  }

  // -- suggesting -------------------------------------------------------------------------

  function scheduleSuggest() {
    clearTimeout(suggestTimer)
    suggestTimer = setTimeout(() => run(suggestNow).catch(fail), AUTOSAVE_MS * 1.5)
  }

  function startSuggesting() {
    view.dispatch({ effects: setSuggestBase.of(view.state.doc.toString()), annotations: remote.of(true) })
  }

  // What was typed while Suggesting becomes suggestions: each changed run of
  // words, as the server's text has it, and what it would become. A run that
  // touches one of my own open suggestions grows it rather than making another.
  async function suggestNow() {
    clearTimeout(suggestTimer)
    const base = view.state.field(suggestBase, false)
    if (base == null) return
    const now = view.state.doc.toString()
    if (now === base) return
    const hunks = diffChanges(base, now)
    const own = st.notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author?.id === st.me?.id && n.where?.state === 'attached')
    let last = null
    // Put the editor back to the text as it is; the suggestions draw over it.
    const caret = view.state.selection.main.head
    const back = ChangeSet.of(diffChanges(now, base), now.length, '\n')
    const caretBack = back.mapPos(caret, 1)
    view.dispatch({ changes: back, selection: { anchor: Math.min(caretBack, base.length) }, effects: setSuggestBase.of(base), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
    view.dispatch({ effects: historyC.reconfigure(history()), annotations: remote.of(true) })
    st.sinceBase = ChangeSet.empty(base.length)
    const bodies = []
    for (const c of hunks) {
      let from = c.from, to = c.to
      let insert = c.insert
      // Whole words, so a suggestion reads as words replaced.
      while (from > 0 && /\w/.test(base[from - 1]) && (to > from ? /\w/.test(base[from]) : /\w/.test(insert[0] || ''))) { insert = base[from - 1] + insert; from-- }
      while (to < base.length && /\w/.test(base[to]) && (to > from ? /\w/.test(base[to - 1]) : /\w/.test(insert[insert.length - 1] || ''))) { insert = insert + base[to]; to++ }
      const near = own.find(n => n.to === from || n.from === to)
      let body
      if (near) {
        const s = near.from, e = near.to
        if (near.to === from) body = { start: s, end: Math.max(e, to), replacement: near.replacement + base.slice(e, from) + insert, supersedes: near.id }
        else body = { start: Math.min(s, from), end: e, replacement: insert + base.slice(to, s) + near.replacement, supersedes: near.id }
      } else if (to === from) {
        // A pure insertion: point at the word before it (or after it at the
        // start), and suggest that word with the insertion.
        let s = from
        while (s > 0 && /\s/.test(base[s - 1])) s--
        let w = s
        while (w > 0 && !/\s/.test(base[w - 1])) w--
        if (w < s) body = { start: w, end: from, replacement: base.slice(w, from) + insert }
        else {
          let e = to
          while (e < base.length && !/\s/.test(base[e])) e++
          if (e === to) continue
          body = { start: from, end: e, replacement: insert + base.slice(from, e) }
        }
      } else {
        body = { start: from, end: to, replacement: insert }
      }
      bodies.push(body)
    }
    // Each is sent in turn. The first note on a doc adds its id line, so the
    // stored text can move between two of them: what is left is mapped
    // through that move before it is sent.
    for (const b of bodies) {
      const moved = ChangeSet.of(diffChanges(base, st.mapBase), base.length, '\n')
      const start = moved.mapPos(b.start, 1), end = moved.mapPos(b.end, -1)
      const body = {
        kind: 'suggestion', text: '', revision: st.server.revision,
        quote: st.pm.slice(start, end), replacement: raw(b.replacement),
        start: st.pm.toServer(start), end: st.pm.toServer(end),
      }
      if (b.supersedes) body.supersedes = b.supersedes
      try {
        last = await api.add(body)
        applyAnswer(last)
        if (last.note) st.peek = null
      } catch (err) {
        fail(err)
      }
    }
    view.dispatch({ effects: setSuggestBase.of(view.state.doc.toString()), annotations: remote.of(true) })
  }

  // -- modes -------------------------------------------------------------------------------

  async function setModeTo(mode) {
    if (mode === st.mode) return
    if ((mode === 'edit' && !st.can.edit) || (mode === 'suggest' && !st.can.suggest)) return
    await run(flushNow).catch(fail)
    st.mode = mode
    view.dispatch({ effects: [setMode.of(mode), editableC.reconfigure(editableExt()), setSuggestBase.of(mode === 'suggest' ? view.state.doc.toString() : null)], annotations: remote.of(true) })
    st.menu = null
    hideSelbar()
    render()
    queueLayout()
  }

  // -- notes: making, replying, closing -----------------------------------------------------

  function selectionRange() {
    const r = view.state.selection.main
    let from = r.from, to = r.to
    const doc = view.state.doc
    while (from < to && /\s/.test(doc.sliceString(from, from + 1))) from++
    while (to > from && /\s/.test(doc.sliceString(to - 1, to))) to--
    return to > from ? { from, to } : null
  }

  function startNote(kind) {
    if (st.reattach) return
    const r = selectionRange()
    if (!r) return
    const quote = view.state.doc.sliceString(r.from, r.to)
    st.compose = { kind, from: r.from, to: r.to, quote, text: '', replacement: quote }
    st.focus = null
    hideSelbar()
    pushNotes()
    view.dispatch({ selection: { anchor: r.to }, annotations: remote.of(true) })
    if (st.phone) openSheet('compose')
    render()
    setTimeout(() => (root.querySelector(kind === 'suggestion' ? '.dc-compose .dc-replace' : '.dc-compose .dc-input'))?.focus(), 30)
  }

  function cancelCompose() {
    st.compose = null
    pushNotes()
    if (st.sheet === 'compose') closeSheet()
    render()
  }

  async function submitCompose() {
    const c = st.compose
    if (!c) return
    const tracked = currentNoteRanges().find(n => n.id === '__new')
    const body = { kind: c.kind, text: c.text || '' }
    if (c.kind === 'suggestion') body.replacement = c.replacement
    if (c.kind === 'comment' && !body.text.trim()) return
    if (c.kind === 'suggestion' && body.replacement === c.quote) { cancelCompose(); return }
    st.compose = { ...c, busy: true }
    render()
    try {
      const ans = await run(async () => {
        await flushNow()
        // The passage, in the server's text: the editor and the server agree
        // after the flush, apart from anything typed since.
        const back = st.sinceBase.invertedDesc
        const from = back.mapPos(tracked?.from ?? c.from, 1)
        const to = back.mapPos(tracked?.to ?? c.to, -1)
        body.start = st.pm.toServer(from)
        body.end = st.pm.toServer(to)
        body.quote = st.pm.slice(from, to)
        if (body.replacement != null) body.replacement = raw(body.replacement)
        body.revision = st.server.revision
        return api.add(body)
      })
      st.compose = null
      if (st.sheet === 'compose') closeSheet()
      st.focus = ans.note
      applyAnswer(ans)
    } catch (err) {
      st.compose = { ...c, busy: false }
      fail(err)
    }
  }

  async function reply(note, text) {
    if (!text.trim()) return
    await call(() => api.reply(note.id, text))
  }

  async function resolve(note) {
    const ans = await call(() => api.resolve(note.id))
    if (ans) {
      st.focus = null
      showToast('Resolved', () => call(() => api.reopen(note.id)), false, true)
      render()
    }
  }

  async function accept(note) {
    const ans = await call(() => api.accept(note.id))
    // Undo puts the words back and the suggestion with them, while the accept
    // is still the doc's last change (the server checks).
    if (ans) { st.focus = null; showToast('Accepted', () => call(() => api.reopen(note.id)), false, true); render() }
  }

  async function reject(note) {
    const ans = await call(() => api.reject(note.id))
    if (ans) { st.focus = null; showToast('Rejected', () => call(() => api.reopen(note.id))); render() }
  }

  async function reopen(note) {
    await call(() => api.reopen(note.id))
  }

  function beginReattach(note) {
    st.reattach = note
    st.drawer = null
    render()
  }

  async function finishReattach() {
    const note = st.reattach
    const r = selectionRange()
    if (!note || !r) return
    st.reattach = null
    hideSelbar()
    await call(async () => {
      const back = st.sinceBase.invertedDesc
      const from = back.mapPos(r.from, 1), to = back.mapPos(r.to, -1)
      return api.reattach(note.id, { start: st.pm.toServer(from), end: st.pm.toServer(to), quote: st.pm.slice(from, to), revision: st.server.revision })
    })
    st.focus = note.id
    pushNotes()
    render()
  }

  function focusNote(id, { scrollTo = false } = {}) {
    if (st.compose && id !== '__new') return
    st.focus = id
    pushNotes()
    render()
    if (scrollTo && id) {
      const n = st.notes.find(x => x.id === id)
      if (n?.from != null) view.dispatch({ effects: EditorView.scrollIntoView(n.from, { y: 'center' }), annotations: remote.of(true) })
    }
    if (st.phone && id) openSheet('thread')
  }

  function escape() {
    if (st.bmenu) { closeBmenu(); return true }
    if (st.link) { st.link = null; hideSelbar(); return true }
    if (st.compose) { cancelCompose(); return true }
    if (st.menu) { st.menu = null; render(); return true }
    if (st.drawer) { st.drawer = null; render(); return true }
    if (st.focus) { st.focus = null; pushNotes(); render(); return true }
    if (st.reattach) { st.reattach = null; render(); return true }
    return false
  }

  // -- editor events: passages and links ------------------------------------------------------

  function noteAt(target) {
    const el = target?.closest?.('[data-note]')
    return el ? el.getAttribute('data-note') : null
  }

  function onEditorMouseDown(e) {
    const wiki = e.target?.closest?.('.dc-wiki[data-wiki]')
    if (wiki && (e.metaKey || e.ctrlKey || st.mode === 'view')) {
      e.preventDefault()
      openLink?.(`[[${wiki.getAttribute('data-wiki')}]]`, e)
      return true
    }
    const link = e.target?.closest?.('.dc-link')
    if (link && (e.metaKey || e.ctrlKey || st.mode === 'view')) {
      const pos = view.posAtDOM(link)
      const url = linkAt(view.state, pos + 1)
      if (url) { e.preventDefault(); openLink?.(url, e); return true }
    }
    return false
  }

  function onEditorClick(e) {
    const id = noteAt(e.target)
    if (id && id !== '__new') {
      const sel = view.state.selection.main
      if (sel.empty || st.mode === 'view' || st.phone) focusNote(id)
    } else if (st.focus && !st.phone && view.state.selection.main.empty) {
      st.focus = null
      pushNotes()
      render()
    }
  }

  function onEditorOver(e) {
    const id = noteAt(e.target)
    if (id && id !== st.peek) { st.peek = id; markPeek() }
  }

  function onEditorOut(e) {
    if (st.peek && !e.relatedTarget?.closest?.(`[data-note="${st.peek}"]`)) { st.peek = null; markPeek() }
  }

  function markPeek() {
    for (const el of margin.querySelectorAll('.dc-card')) el.classList.toggle('hov', el.dataset.id === st.peek)
    for (const el of view.contentDOM.querySelectorAll('[data-note]')) el.classList.toggle('peek', el.getAttribute('data-note') === st.peek && st.focus !== st.peek)
  }

  // -- the selection popup (desktop) -----------------------------------------------------------

  let selbarQueued = false
  function queueSelbar() {
    if (selbarQueued) return
    selbarQueued = true
    requestAnimationFrame(() => { selbarQueued = false; renderSelbar() })
  }

  function hideSelbar() {
    selbar.classList.remove('on')
    st.link = null
  }

  function tip(label, md, key) {
    return h('span', { class: 'dc-tip' }, h('span', { text: label }), md ? h('code', { text: md }) : null, key ? h('kbd', { text: key.replace('Mod', MOD).replace(/ /g, ' ') }) : null)
  }

  function renderSelbar() {
    if (st.phone || st.destroyed) return hideSelbar()
    const r = selectionRange()
    const focused = view.hasFocus
    if (st.link) return renderLinkBar()
    if (!r || !focused || st.compose) return hideSelbar()
    const a = active(view.state)
    const items = []
    if (st.reattach) {
      items.push(h('button', { class: 'dc-sx', type: 'button', onclick: finishReattach }, icon('reattach', 14), h('span', { text: 'Attach the note here' })))
    } else {
      if (st.mode === 'edit' && st.can.edit) {
        items.push(
          h('button', { class: 'dc-fb aa', type: 'button', 'aria-label': 'Text style', onclick: (e) => openBmenu({ at: 'selbar', anchor: e.currentTarget }) }, h('span', { text: 'Aa' }), icon('chevronDown', 12), tip('Text style', '#  ##  -  >', null)),
          h('span', { class: 'dc-sep' }),
          ...['bold', 'italic', 'strike'].map(k => h('button', { class: `dc-fb${a[k] ? ' on' : ''}`, type: 'button', 'aria-label': INLINE[k].label, 'aria-pressed': String(a[k]), onclick: () => { toggleInline(view, k); queueSelbar() } }, icon(k, 16), tip(INLINE[k].label, INLINE[k].md, `${MOD} ${INLINE[k].key}`))),
          h('button', { class: `dc-fb${a.link ? ' on' : ''}`, type: 'button', 'aria-label': 'Link', onclick: openLink_ }, icon('link', 16), tip('Link', '[text](url)', `${MOD} K`)),
          h('button', { class: `dc-fb${a.code ? ' on' : ''}`, type: 'button', 'aria-label': 'Code', onclick: () => { toggleInline(view, 'code'); queueSelbar() } }, icon('code', 16), tip('Code', '`code`', `${MOD} E`)),
          h('span', { class: 'dc-sep' }),
        )
      }
      if (st.can.comment) items.push(h('button', { class: 'dc-sx', type: 'button', onclick: () => startNote('comment') }, icon('comment', 14), h('span', { text: 'Comment' }), tip('Comment', null, `${MOD} Alt M`)))
      if (st.can.suggest && st.mode !== 'view') items.push(h('button', { class: 'dc-sx', type: 'button', onclick: () => startNote('suggestion') }, icon('suggest', 14), h('span', { text: 'Suggest' }), tip('Suggest a change', null, null)))
    }
    if (!items.length) return hideSelbar()
    selbar.className = `dc-selbar${st.mode === 'edit' && st.can.edit && !st.reattach ? ' fmt' : ''}`
    put(selbar, ...items)
    placeAbove(selbar, r.from, r.to)
    selbar.classList.add('on')
  }

  function placeAbove(el, from, to) {
    const ca = view.coordsAtPos(from), cb = view.coordsAtPos(to, -1)
    if (!ca || !cb) return
    const a = local(ca), b = local(cb), c = local(col.getBoundingClientRect())
    const box = rootSize()
    el.style.visibility = 'hidden'
    el.classList.add('on')
    const w = el.offsetWidth, hgt = el.offsetHeight
    el.style.visibility = ''
    const sameLine = Math.abs(a.top - b.top) < 4
    const mid = sameLine ? (a.left + b.right) / 2 : (a.left + Math.min(b.right, c.right)) / 2
    let left = mid - w / 2
    left = Math.max(8, Math.min(left, box.width - w - 8))
    // Just above the first line of the selection, clear of the header;
    // below its last line when there is no room above.
    let top = a.top - hgt - 8
    if (top < head.offsetHeight + 4) top = b.bottom + 8
    el.style.left = `${left}px`
    el.style.top = `${top}px`
  }

  function openLink_() {
    const r = view.state.selection.main
    st.link = { from: r.from, to: r.to, url: linkAt(view.state, r.from) || '' }
    renderLinkBar()
  }

  function renderLinkBar() {
    const l = st.link
    const input = h('input', { type: 'url', placeholder: 'Paste or type a link', value: l.url, 'aria-label': 'Link address' })
    const done = () => {
      const url = input.value.trim()
      st.link = null
      hideSelbar()
      setLink(view, url, { from: l.from, to: l.to })
    }
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); done() }
      if (e.key === 'Escape') { e.preventDefault(); st.link = null; hideSelbar(); view.focus() }
    })
    selbar.className = 'dc-selbar lnk'
    put(selbar, h('span', { class: 'dc-lk' }, icon('link', 15)), input, h('button', { class: 'dc-fb ok', type: 'button', 'aria-label': 'Apply link', onclick: done }, icon('check', 14)))
    placeAbove(selbar, l.from, l.to)
    selbar.classList.add('on')
    setTimeout(() => input.focus(), 0)
  }

  // -- the block menu: + and / ----------------------------------------------------------------

  function emptyLineAtCaret() {
    const r = view.state.selection.main
    if (!r.empty || !view.hasFocus || st.mode !== 'edit' || !st.can.edit) return null
    const line = view.state.doc.lineAt(r.head)
    return line.text.trim() === '' ? line : null
  }

  function renderPlus() {
    const line = st.phone ? null : emptyLineAtCaret()
    if (!line) { plus.classList.remove('on'); return }
    const block = view.lineBlockAt(line.from)
    const z = zoom()
    plus.style.top = `${block.top / z + (block.height / z - 26) / 2}px`
    plus.classList.add('on')
  }

  plus.addEventListener('mousedown', (e) => e.preventDefault())
  plus.addEventListener('click', () => {
    if (st.bmenu) return closeBmenu()
    openBmenu({ at: 'plus' })
  })

  function onBeforeSlash() { /* the slash is read in onUpdate through closeBmenuIfStale */ }

  // "/" typed on an empty line opens the menu; what is typed after it filters.
  view.dom.addEventListener('keyup', (e) => {
    if (e.key !== '/' || st.mode !== 'edit' || st.phone) return
    const r = view.state.selection.main
    const line = view.state.doc.lineAt(r.head)
    if (line.text.trim() === '/' && r.head === line.from + line.text.indexOf('/') + 1) {
      openBmenu({ at: 'slash', slashAt: r.head - 1 })
    }
  })

  function closeBmenuIfStale() {
    const b = st.bmenu
    if (!b || b.at !== 'slash') return
    const line = view.state.doc.lineAt(view.state.selection.main.head)
    const t = line.text.trim()
    if (!t.startsWith('/')) return closeBmenu()
    b.query = t.slice(1).toLowerCase()
    b.i = 0
    renderBmenu()
  }

  function openBmenu(spec) {
    st.bmenu = { ...spec, query: '', i: 0 }
    plus.classList.add('open')
    renderBmenu()
  }

  function closeBmenu() {
    st.bmenu = null
    plus.classList.remove('open')
    bmenu.classList.remove('on')
  }

  // From a selection, the menu turns what is selected into another kind of
  // block; a code block, a divider and a table are added below rather than
  // turned into, so they are offered only where a block is being added.
  const ADDS = new Set(['code', 'hr', 'table'])
  function turnable() {
    return BLOCKS.filter(b => !ADDS.has(b.id))
  }

  function bmenuItems() {
    const q = st.bmenu?.query || ''
    const list = st.bmenu?.at === 'selbar' ? turnable() : BLOCKS
    return list.filter(b => !q || b.label.toLowerCase().includes(q) || b.id.startsWith(q))
  }

  function chooseBlock(b) {
    const spec = st.bmenu
    closeBmenu()
    if (spec?.at === 'slash') {
      const line = view.state.doc.lineAt(view.state.selection.main.head)
      view.dispatch({ changes: { from: line.from, to: line.to, insert: '' }, selection: { anchor: line.from } })
    }
    setBlock(view, b.id)
    hideSelbar()
  }

  function renderBmenu() {
    const b = st.bmenu
    if (!b) return
    const items = bmenuItems()
    const current = blockOf(view.state)
    if (b.i >= items.length) b.i = Math.max(0, items.length - 1)
    put(bmenu, 
      b.at === 'slash' && b.query ? null : h('div', { class: 'dc-bsec', text: b.at === 'selbar' ? 'Turn into' : 'Add a block' }),
      ...items.map((it, i) => h('button', {
        class: `dc-bi${i === b.i ? ' on' : ''}`, type: 'button', role: 'menuitem',
        onmouseenter: () => { b.i = i; for (const [j, el] of [...bmenu.querySelectorAll('.dc-bi')].entries()) el.classList.toggle('on', j === i) },
        onclick: () => chooseBlock(it),
      },
      h('span', { class: `dc-bt${it.glyph ? ' g' : ''}` }, it.icon ? icon(it.icon, 15) : it.glyph),
      h('span', { class: 'dc-bl', text: it.label }),
      b.at === 'selbar' && current === it.id ? h('span', { class: 'dc-bck' }, icon('check', 14)) : h('span', { class: 'dc-bk', text: it.md.trim() || '' }))),
      items.length ? null : h('div', { class: 'dc-bn', text: 'No block by that name' }),
    )
    // Below what opened it when every row fits there, above when they fit
    // there instead, and otherwise on the roomier side, scrolling only then.
    const box = rootSize()
    let below, above, left
    if (b.at === 'selbar' && b.anchor) {
      const r = local(b.anchor.getBoundingClientRect())
      below = r.bottom + 6
      above = r.top - 6
      left = r.left - 4
    } else {
      const c0 = view.coordsAtPos(view.state.selection.main.head)
      if (!c0) return
      const c = local(c0)
      below = c.bottom + 6
      above = c.top - 6
      left = b.at === 'plus' ? local(plus.getBoundingClientRect()).left : c.left
    }
    bmenu.style.maxHeight = ''
    bmenu.classList.add('on')
    const hgt = bmenu.scrollHeight
    const floor = head.offsetHeight + 6
    const roomBelow = box.height - 12 - below, roomAbove = above - floor
    let top
    if (hgt <= roomBelow) top = below
    else if (hgt <= roomAbove) top = above - hgt
    else if (roomBelow >= roomAbove) { top = below; bmenu.style.maxHeight = `${roomBelow}px` }
    else { top = floor; bmenu.style.maxHeight = `${roomAbove}px` }
    bmenu.classList.toggle('up', top < below)
    bmenu.style.top = `${top}px`
    bmenu.style.left = `${Math.max(8, Math.min(left, box.width - bmenu.offsetWidth - 8))}px`
  }

  view.dom.addEventListener('keydown', (e) => {
    const b = st.bmenu
    if (!b) return
    const items = bmenuItems()
    if (e.key === 'ArrowDown') { e.preventDefault(); b.i = (b.i + 1) % Math.max(1, items.length); renderBmenu() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); b.i = (b.i - 1 + items.length) % Math.max(1, items.length); renderBmenu() }
    else if (e.key === 'Enter' && items[b.i]) { e.preventDefault(); e.stopPropagation(); chooseBlock(items[b.i]) }
    else if (e.key === 'Escape') { e.preventDefault(); closeBmenu() }
  }, true)

  // -- the margin --------------------------------------------------------------------------------

  function openNotes() {
    return st.notes.filter(n => n.state === 'open' && n.from != null && n.where?.state !== 'detached')
  }

  function detached() {
    return st.notes.filter(n => n.state === 'open' && n.where?.state === 'detached')
  }

  function name(author) {
    const p = people_(author)
    return p.id && st.me && p.id === st.me.id && p.kind !== 'agent' ? p.name : p.name
  }

  function avatar(author, size) {
    return h('span', { class: 'dc-av', style: { width: `${size}px`, height: `${size}px` } }, face(people_(author), size))
  }

  function cardHead(n, focused) {
    const isComment = n.kind === 'comment'
    const changed = n.where?.state === 'changed'
    return h('div', { class: 'dc-ch' },
      avatar(n.author, 20),
      h('span', { class: 'dc-nm', text: name(n.author) }),
      focused ? h('span', { class: 'dc-tm', title: stamp(n.created), text: rel(n.created) }) : null,
      changed ? h('span', { class: 'dc-chg', title: `Text changed since. It read: “${n.anchor.quote}”` }, icon('changed', 14)) : null,
      h('span', { class: 'dc-grow' }),
      focused && mine(n) && !st.editing ? ownTools(n) : null,
      focused && isComment && mayResolve(n) ? h('button', { class: 'dc-ib dc-resolve', type: 'button', title: 'Resolve', 'aria-label': 'Resolve', onclick: (e) => { e.stopPropagation(); resolve(n) } }, icon('check', 15)) : null,
    )
  }

  // One's own words: the note's (from its head) and each reply's.
  function mine(item) {
    return Boolean(item?.author?.id && st.me?.id && item.author.id === st.me.id)
  }

  function ownTools(n, r = null) {
    return h('span', { class: 'dc-own' },
      h('button', { class: 'dc-ib', type: 'button', title: 'Change your words', 'aria-label': 'Change your words', onclick: (e) => { e.stopPropagation(); st.editing = { id: n.id, rid: r ? r.id : null }; render(); setTimeout(() => root.querySelector('.dc-edit-fld textarea')?.focus(), 30) } }, icon('pencil', 13)),
      r ? h('button', { class: 'dc-ib', type: 'button', title: 'Remove your reply', 'aria-label': 'Remove your reply', onclick: (e) => { e.stopPropagation(); removeReply(n, r) } }, icon('trash', 13)) : null)
  }

  function editField(n, rid, text) {
    const t = h('textarea', { rows: '1', 'aria-label': 'Your words' })
    t.value = text
    const done = async (keep) => {
      const next = t.value
      st.editing = null
      if (keep && next.trim() !== (text || '').trim()) await call(() => api.edit(n.id, rid, next))
      else render()
    }
    t.addEventListener('input', () => fit(t))
    t.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); done(true) }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false) }
    })
    setTimeout(() => fit(t), 0)
    return h('div', { class: 'dc-edit-fld' },
      h('div', { class: 'dc-fld' }, t),
      h('div', { class: 'dc-dbtns' },
        h('button', { class: 'dc-sm ghost', type: 'button', onclick: (e) => { e.stopPropagation(); done(false) } }, 'Cancel'),
        h('button', { class: 'dc-sm ok', type: 'button', onclick: (e) => { e.stopPropagation(); done(true) } }, 'Save')))
  }

  async function removeReply(n, r) {
    const ans = await call(() => api.deleteReply(n.id, r.id))
    if (ans) showToast('Reply removed')
  }

  function mayResolve(n) {
    return st.can.resolve_any || n.author?.id === st.me?.id
  }

  // What a suggestion does, in words: an insertion says what it adds, a
  // deletion what it takes out, anything else what the passage becomes.
  function suggestionLine(n) {
    const from = n.anchor?.quote ?? ''
    const shape = suggestionShape(from, n.replacement ?? '')
    if (shape.kind === 'insert') return h('div', { class: 'dc-stl' }, h('span', { text: 'Add ' }), h('ins', { text: `“${clip(shape.insert)}”` }))
    if (shape.kind === 'delete') return h('div', { class: 'dc-stl' }, h('span', { text: 'Delete ' }), h('del', { text: `“${clip(from.slice(shape.from, shape.to))}”` }))
    return h('div', { class: 'dc-stl' }, h('span', { text: 'Replace with ' }), h('ins', { text: `“${clip(n.replacement)}”` }))
  }

  function clip(s, n = 90) {
    s = (s || '').replace(/\s+/g, ' ').trim()
    return s.length > n ? s.slice(0, n - 1) + '…' : s
  }

  function bodyText(n) {
    if (n.kind === 'suggestion' && n.origin === 'conflict' && !n.text) return 'Saved at the same moment as another edit to these words, so it waits here as a suggestion.'
    return n.text
  }

  function replyField(n) {
    const input = h('input', { class: 'dc-input', type: 'text', placeholder: 'Reply', 'aria-label': 'Reply' })
    const wrap = h('div', { class: 'dc-cmp' })
    const send = h('button', { class: 'dc-send', type: 'button', 'aria-label': 'Send reply', onclick: async () => { const t = input.value; input.value = ''; wrap.classList.remove('has'); await reply(n, t) } }, icon('send', 15))
    input.addEventListener('input', () => wrap.classList.toggle('has', Boolean(input.value.trim())))
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click() }
      if (e.key === 'Escape') { e.preventDefault(); escape() }
    })
    wrap.append(input, send)
    return wrap
  }

  function threadBody(n, { full }) {
    const out = []
    if (n.kind === 'suggestion') out.push(suggestionLine(n))
    const text = bodyText(n)
    const editingNote = full && st.editing?.id === n.id && st.editing.rid == null
    if (editingNote) out.push(editField(n, null, n.text || ''))
    else if (text) out.push(h('p', { class: `dc-tx${full ? '' : ' clamp'}`, text }))
    // The glyph in the head says it at rest; the focused note says it in
    // words, where a phone can read it too.
    if (full && n.where?.state === 'changed') out.push(h('div', { class: 'dc-chgl' }, 'The words changed since. They read ', h('q', { text: clip(n.anchor?.quote || '', 120) })))
    if (!full) {
      if (n.replies?.length) out.push(h('div', { class: 'dc-more', text: n.replies.length === 1 ? '1 reply' : `${n.replies.length} replies` }))
      return out
    }
    if (n.replies?.length) {
      out.push(h('div', { class: 'dc-rps' }, n.replies.map(r => h('div', { class: 'dc-rp' },
        avatar(r.author, 18),
        h('div', {},
          h('div', { class: 'dc-rh' }, h('b', { text: name(r.author) }), h('span', { class: 'dc-tm', title: stamp(r.created), text: ` ${rel(r.created)}` }), mine(r) && !st.editing ? ownTools(n, r) : null),
          st.editing?.id === n.id && st.editing.rid === r.id ? editField(n, r.id, r.text) : h('div', { class: 'dc-rt', text: r.text }))))))
    }
    if (n.kind === 'suggestion' && st.can.accept) {
      out.push(h('div', { class: 'dc-dbtns' },
        h('button', { class: 'dc-sm', type: 'button', onclick: (e) => { e.stopPropagation(); reject(n) } }, 'Reject'),
        h('button', { class: 'dc-sm ok', type: 'button', disabled: n.where?.state !== 'attached' ? true : null, title: n.where?.state === 'changed' ? 'The words changed since this was suggested' : null, onclick: (e) => { e.stopPropagation(); accept(n) } }, 'Accept')))
    }
    if (st.can.comment) out.push(replyField(n))
    return out
  }

  function card(n) {
    const focused = st.focus === n.id
    const el = cardEls.get(n.id) || h('div', { class: 'dc-card', dataset: { id: n.id } })
    cardEls.set(n.id, el)
    el.className = `dc-card${focused ? ' on' : ''}${st.peek === n.id ? ' hov' : ''}${n.kind === 'suggestion' ? ' sug' : ''}`
    el.onclick = (e) => { if (!focused && !e.target.closest('button,input')) focusNote(n.id) }
    el.onmouseenter = () => { st.peek = n.id; markPeek() }
    el.onmouseleave = () => { st.peek = null; markPeek() }
    put(el, cardHead(n, focused), ...threadBody(n, { full: focused }))
    return el
  }

  function composeCard() {
    const c = st.compose
    const el = cardEls.get('__new') || h('div', { class: 'dc-card on dc-compose', dataset: { id: '__new' } })
    cardEls.set('__new', el)
    el.className = 'dc-card on dc-compose'
    const kids = [h('div', { class: 'dc-ch' }, avatar(st.me, 20), h('span', { class: 'dc-nm', text: name(st.me) }), h('span', { class: 'dc-tm', text: c.kind === 'suggestion' ? 'suggesting an edit' : 'new comment' }))]
    if (c.kind === 'suggestion') {
      const rep = h('textarea', { class: 'dc-replace', rows: '1', 'aria-label': 'Replace with' })
      rep.value = c.replacement
      rep.addEventListener('input', () => { c.replacement = rep.value; fit(rep) })
      rep.addEventListener('keydown', keyCompose)
      const why = h('input', { class: 'dc-input dc-why', type: 'text', placeholder: 'Why (optional)', 'aria-label': 'Why' })
      why.value = c.text
      why.addEventListener('input', () => { c.text = why.value })
      why.addEventListener('keydown', keyCompose)
      kids.push(h('div', { class: 'dc-lbl', text: 'Replace with' }), h('div', { class: 'dc-fld' }, rep), h('div', { class: 'dc-fld' }, why))
      setTimeout(() => fit(rep), 0)
    } else {
      const input = h('textarea', { class: 'dc-input', rows: '1', placeholder: 'Add a comment', 'aria-label': 'Comment' })
      input.value = c.text
      input.addEventListener('input', () => { c.text = input.value; fit(input); btn.disabled = !input.value.trim() })
      input.addEventListener('keydown', keyCompose)
      kids.push(h('div', { class: 'dc-fld big' }, input))
    }
    const btn = h('button', { class: 'dc-sm ok', type: 'button', disabled: c.busy || (c.kind === 'comment' && !c.text.trim()) ? true : null, onclick: submitCompose }, c.kind === 'suggestion' ? 'Suggest' : 'Comment')
    kids.push(h('div', { class: 'dc-dbtns' }, h('button', { class: 'dc-sm ghost', type: 'button', onclick: cancelCompose }, 'Cancel'), btn))
    put(el, ...kids)
    return el
  }

  function keyCompose(e) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey || (!e.shiftKey && e.target.tagName === 'INPUT') || (!e.shiftKey && e.target.classList.contains('dc-input')))) { e.preventDefault(); submitCompose() }
    if (e.key === 'Escape') { e.preventDefault(); cancelCompose() }
  }

  function fit(t) {
    t.style.height = 'auto'
    t.style.height = `${Math.min(220, t.scrollHeight)}px`
  }

  const cardEls = new Map()

  function renderMargin() {
    const list = openNotes().sort((a, b) => a.from - b.from)
    const wanted = new Set(list.map(n => n.id))
    if (st.compose) wanted.add('__new')
    for (const [id, el] of cardEls) if (!wanted.has(id)) { el.remove(); cardEls.delete(id) }
    const els = list.map(card)
    if (st.compose) {
      const ce = composeCard()
      const at = list.findIndex(n => n.from > st.compose.from)
      els.splice(at < 0 ? els.length : at, 0, ce)
    }
    for (const el of els) if (el.parentNode !== margin) margin.append(el)
    margin.classList.toggle('hasf', Boolean(st.focus || st.compose))
    queueLayout()
  }

  function queueLayout() {
    if (layoutQueued) return
    layoutQueued = true
    requestAnimationFrame(() => { layoutQueued = false; layout() })
  }

  // The top of the words a passage starts with, in the page's pixels from the
  // top of `.dc-page` (where the margin's cards are placed from): measured
  // where the line is drawn, else from CodeMirror's height map.
  function anchorTop(pos) {
    const z = zoom()
    const pageTop = page.getBoundingClientRect().top
    const c = view.coordsAtPos(pos, 1)
    if (c) return (c.top - pageTop) / z
    return (view.documentTop - pageTop + view.lineBlockAt(pos).top) / z + 4
  }

  function layout() {
    if (st.destroyed || !root.isConnected) return
    const ranges = new Map(currentNoteRanges().map(n => [n.id, n]))
    if (!st.phone && st.mode !== 'view') {
      const items = []
      for (const [id, el] of cardEls) {
        const r = ranges.get(id)
        if (!r || r.from == null) continue
        // The card's head row (below its 10 px of padding) level with the
        // first line of its passage.
        items.push({ id, el, want: anchorTop(r.from) - 10, hgt: el.offsetHeight })
      }
      items.sort((a, b) => a.want - b.want)
      const gap = 10
      const fi = items.findIndex(i => i.id === (st.compose ? '__new' : st.focus))
      if (fi >= 0) {
        items[fi].top = items[fi].want
        for (let i = fi - 1; i >= 0; i--) items[i].top = Math.min(items[i].want, items[i + 1].top - gap - items[i].hgt)
        for (let i = fi + 1; i < items.length; i++) items[i].top = Math.max(items[i].want, items[i - 1].top + items[i - 1].hgt + gap)
      } else {
        let floor = -Infinity
        for (const it of items) { it.top = Math.max(it.want, floor); floor = it.top + it.hgt + gap }
      }
      let lowest = 0
      for (const it of items) {
        it.el.style.top = `${Math.round(it.top)}px`
        lowest = Math.max(lowest, it.top + it.hgt)
      }
      margin.style.minHeight = `${Math.ceil(lowest + 40)}px`
    }
    renderGutter(ranges)
    renderPlus()
    if (selbar.classList.contains('on') && !st.link) renderSelbar()
    if (st.mode === 'view' && st.focus && !st.phone) placeQpop()
  }

  // Viewing (desktop) and the phone: small marks in the gutter, one per line
  // with notes; a click opens the thread.
  function renderGutter(ranges) {
    const show = st.phone || st.mode === 'view'
    if (!show) { put(gutter, ); return }
    const byLine = new Map()
    for (const n of openNotes()) {
      const r = ranges.get(n.id)
      if (!r || r.from == null) continue
      const top = Math.round(lineTop(r.from))
      if (!byLine.has(top)) byLine.set(top, [])
      byLine.get(top).push(n)
    }
    const marks = []
    for (const [top, ns] of byLine) {
      const on = ns.some(n => n.id === st.focus)
      const m = h('button', { class: `dc-mk${on ? ' on' : ''}`, type: 'button', 'aria-label': `${ns.length} note${ns.length > 1 ? 's' : ''}`, style: { top: `${top}px` }, onclick: () => focusNote(on ? null : ns[0].id) },
        st.phone ? null : avatar(ns[0].author, 18), !st.phone && ns.length > 1 ? h('span', { text: String(ns.length) }) : null)
      marks.push(m)
    }
    put(gutter, ...marks)
  }

  function placeQpop() {
    const n = st.notes.find(x => x.id === st.focus)
    const r = currentNoteRanges().find(x => x.id === st.focus)
    if (!n || !r) { qpop.classList.remove('on'); return }
    put(qpop, h('div', { class: 'dc-card on pop' }, cardHead(n, true), ...threadBody(n, { full: true })))
    const box = rootSize()
    const c0 = view.coordsAtPos(r.to, -1) || view.coordsAtPos(r.from)
    if (!c0) return
    const c = local(c0)
    qpop.classList.add('on')
    const w = qpop.offsetWidth
    let left = Math.min(c.left - 20, box.width - w - 16)
    left = Math.max(16, left)
    let top = c.bottom + 10
    if (top + qpop.offsetHeight > box.height - 10) top = Math.max(head.offsetHeight + 8, c.top - qpop.offsetHeight - 10)
    qpop.style.left = `${left}px`
    qpop.style.top = `${top}px`
  }

  // -- the header --------------------------------------------------------------------------------

  function saveWordNode() {
    if (st.mode !== 'edit') return null
    let word = ''
    let tone = 'quiet'
    if (st.saving) word = 'Saving…'
    else if (st.word === 'Couldn’t save') { word = st.word; tone = 'bad' }
    else if (!st.sinceBase.empty) word = 'Edited'
    else if (st.word) word = st.word
    return word ? h('span', { class: `dc-saved ${tone}`, role: 'status', title: tone === 'bad' ? st.error : null, text: word }) : null
  }

  function renderSaveWord() {
    const old = head.querySelector('.dc-saveslot')
    if (old) put(old, saveWordNode() || '')
  }

  function menuWrap(id, button, menu) {
    const open = st.menu === id
    return h('div', { class: `dc-ddw${open ? ' open' : ''}` }, button, open ? menu : null)
  }

  function modeMenu() {
    return h('div', { class: 'dc-menu', role: 'menu' }, MODES.map(m => {
      const allowed = m.id === 'view' || (m.id === 'edit' ? st.can.edit : st.can.suggest)
      return h('button', { class: `dc-mi top${st.mode === m.id ? ' on' : ''}`, type: 'button', role: 'menuitemradio', 'aria-checked': String(st.mode === m.id), disabled: allowed ? null : true, onclick: () => setModeTo(m.id) },
        icon(m.icon, 17), h('span', { class: 'dc-l' }, h('b', { text: m.label }), h('small', { text: m.hint })), h('span', { class: 'dc-ck' }, icon('check', 16)))
    }))
  }

  function moreMenu() {
    const det = detached().length
    return h('div', { class: 'dc-menu', role: 'menu' },
      h('button', { class: `dc-mi${st.authors ? ' on' : ''}`, type: 'button', role: 'menuitemcheckbox', 'aria-checked': String(st.authors), onclick: toggleAuthors },
        icon('people', 17), h('span', { class: 'dc-l' }, h('b', { text: 'Show authors' })), h('span', { class: `dc-sw${st.authors ? ' on' : ''}` })),
      h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: () => openDrawer('history') },
        icon('history', 17), h('span', { class: 'dc-l' }, h('b', { text: 'History' })), det ? h('span', { class: 'dc-ct', text: `${det} detached` }) : null, h('span', { class: 'dc-chv' }, icon('chevronRight', 14))),
      h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: () => openDrawer('resolved') },
        icon('resolved', 17), h('span', { class: 'dc-l' }, h('b', { text: 'Resolved' })), h('span', { class: 'dc-ct', text: String(st.resolved || 0) }), h('span', { class: 'dc-chv' }, icon('chevronRight', 14))),
      ...(opts.menuExtras?.() || []).map(x => h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: () => { st.menu = null; render(); x.run() } }, icon(x.icon || 'file', 17), h('span', { class: 'dc-l' }, h('b', { text: x.label })))),
      fileMenu ? h('div', { class: 'dc-msep' }) : null,
      fileMenu ? h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: () => { st.menu = null; render(); fileMenu() } }, icon('file', 17), h('span', { class: 'dc-l' }, h('b', { text: 'This file…' }), h('small', { text: 'Rename, move, copy link, download' }))) : null,
    )
  }

  function toggleMenu(id) {
    st.menu = st.menu === id ? null : id
    render()
  }

  function renderHead() {
    const m = MODES.find(x => x.id === st.mode)
    // The phone shows the two folders the doc is in, as the mockup does; the
    // desktop the whole path (shortened by the host).
    const all = opts.crumbs || []
    const shown = st.phone && all.length > 2 ? all.slice(0, -1).filter(c => c.label !== '…').slice(-2) : all
    const crumbs = shown.flatMap((c, i) => [
      i ? h('span', { class: 'dc-sl', text: '/' }) : null,
      c.go ? h('button', { class: 'dc-crumb-b', type: 'button', onclick: c.go, text: c.label }) : h('span', { text: c.label }),
    ])
    const pill = h('button', { class: 'dc-mpill', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': String(st.menu === 'mode'), 'aria-label': `Mode: ${m.label}`, onclick: () => toggleMenu('mode') },
      icon(m.icon, 15), h('span', { text: m.label }), h('span', { class: 'dc-cv' }, icon('chevronDown', 12)))
    const dots = h('button', { class: 'dc-gbtn', type: 'button', 'aria-label': 'More', title: 'More', 'aria-haspopup': 'menu', onclick: () => toggleMenu('more') }, icon('dots', 18))
    head.className = `dc-head${scroll.scrollTop > 4 ? ' scd' : ''}`
    put(head, 
      opts.back ? h('button', { class: 'dc-gbtn dc-back', type: 'button', 'aria-label': opts.back.label, title: opts.back.label, onclick: opts.back.go }, icon('chevronLeft', 20)) : null,
      h('div', { class: 'dc-crumb' }, crumbs),
      h('span', { class: 'dc-saveslot' }, saveWordNode()),
      ...(opts.headExtras?.() || []),
      menuWrap('mode', pill, modeMenu()),
      menuWrap('more', dots, moreMenu()),
      ...(opts.headEnd?.() || []),
    )
  }

  scroll.addEventListener('scroll', () => {
    head.classList.toggle('scd', scroll.scrollTop > 4)
    if (!st.phone && selbar.classList.contains('on')) {
      if (st.link) renderLinkBar(); else renderSelbar()
    }
    if (st.bmenu) renderBmenu()
    if (st.mode === 'view' && st.focus && !st.phone) placeQpop()
  }, { passive: true })

  root.addEventListener('mousedown', (e) => {
    if (st.menu && !e.target.closest('.dc-ddw')) { st.menu = null; render() }
    if (st.bmenu && !e.target.closest('.dc-bmenu,.dc-plus,.dc-fb.aa,.dc-kb-aa')) closeBmenu()
    if (e.target.closest('.dc-selbar,.dc-bmenu,.dc-plus,.dc-kbar,.dc-vbar') && !e.target.closest('input')) e.preventDefault()
    if (st.mode === 'view' && st.focus && !st.phone && !e.target.closest('.dc-qpop,.dc-mk,[data-note]')) { st.focus = null; pushNotes(); render() }
  })

  // -- drawers: History and Resolved ----------------------------------------------------------------

  async function openDrawer(which) {
    st.menu = null
    st.drawer = which
    if (st.phone) openSheet(which)
    render()
    if (which === 'history') await loadHistory()
  }

  async function loadHistory() {
    try {
      const ans = await run(() => api.history())
      st.history = ans
      for (const [id, p] of Object.entries(ans.people || {})) people[id] = { ...p, ...(people[id] || {}) }
      render()
    } catch (err) {
      fail(err)
    }
  }

  function saveTitle(s) {
    const sum = s.summary || {}
    const where = sum.section ? `“${clip(sum.section, 44)}”` : sum.first ? `“${clip(sum.first, 40)}”` : ''
    if (s.kind === 'base') return s.folded ? 'Earlier versions, folded' : 'Where the record begins'
    if (s.kind === 'restore' && s.undid) return 'Undid accepting a suggestion'
    if (s.kind === 'restore') return 'Restored an earlier version'
    if (s.kind === 'accept') return where ? `Accepted a suggestion in ${where}` : 'Accepted a suggestion'
    if (sum.frontmatter && !where) return 'Changed the properties'
    if (!where) return 'Edited'
    if (sum.added && !sum.removed) return `Wrote in ${where}`
    if (sum.removed && !sum.added) return `Cut from ${where}`
    return `Edited ${where}`
  }

  function historyView() {
    const H = st.history
    // A version is read before it is restored: a click opens it, with what
    // differs from now marked, and Restore is there.
    const rows = (H?.saves || []).flatMap((s, i) => [h('div', {
      class: `dc-hrow${i === 0 ? ' now' : ''}${st.preview?.id === s.id ? ' sel' : ''}`,
      role: i === 0 ? null : 'button', tabindex: i === 0 ? null : '0', 'aria-expanded': i === 0 ? null : String(st.preview?.id === s.id),
      onclick: i === 0 ? null : () => togglePreview(s),
      onkeydown: i === 0 ? null : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); togglePreview(s) } },
    },
      avatar(s.author, 22),
      h('div', { class: 'dc-l' },
        h('b', { text: saveTitle(s) }),
        h('small', { text: [name(s.author), stamp(s.at), s.kind === 'outside' ? 'outside the editor' : null, s.by && s.by.id !== s.author?.id ? `accepted by ${name(s.by)}` : null].filter(Boolean).join(' · ') })),
      i === 0 ? h('span', { class: 'dc-now', text: 'Now' }) : h('span', { class: 'dc-chv' }, icon(st.preview?.id === s.id ? 'chevronDown' : 'chevronRight', 14))),
      st.preview?.id === s.id ? previewView(s, H.saves[0]) : null])
    const det = detached()
    return [
      H ? null : h('div', { class: 'dc-quiet', text: 'Reading the history…' }),
      H && !H.saves.length ? h('div', { class: 'dc-quiet', text: 'This doc has no history yet. It starts at its first note.' }) : null,
      ...rows,
      det.length ? h('div', { class: 'dc-dsec', text: `Detached · ${det.length}` }) : null,
      ...det.map(n => h('div', { class: 'dc-det' },
        h('div', { class: 'dc-qt gone', text: n.anchor.quote }),
        h('div', { class: 'dc-gone', text: 'Passage gone' }),
        h('div', { class: 'dc-ch' }, avatar(n.author, 20), h('span', { class: 'dc-nm', text: name(n.author) }), h('span', { class: 'dc-tm', text: rel(n.created) })),
        bodyText(n) ? h('p', { class: 'dc-tx', text: bodyText(n) }) : null,
        n.kind === 'suggestion' ? suggestionLine(n) : null,
        h('div', { class: 'dc-dbtns' }, st.can.comment ? h('button', { class: 'dc-sm', type: 'button', onclick: () => beginReattach(n) }, 'Re-attach') : null))),
    ]
  }

  async function togglePreview(s) {
    if (st.preview?.id === s.id) { st.preview = null; render(); return }
    st.preview = { id: s.id, text: null }
    render()
    try {
      const ans = await api.version(s.id)
      if (st.preview?.id === s.id) { st.preview = { id: s.id, text: ans.text }; render() }
    } catch (err) {
      st.preview = null
      fail(err)
    }
  }

  // The version as it was, around what differs from the text now: its words
  // that are gone now are marked, and words added since are shown struck.
  function previewView(s, head_) {
    const p = st.preview
    const body = []
    if (p.text == null) body.push(h('div', { class: 'dc-quiet', text: 'Reading that version…' }))
    else {
      const now = st.server.text
      const hunks = diffChanges(now, p.text)
      const CONTEXT = 140
      const pre = h('pre')
      if (!hunks.length) pre.append('The same as now.')
      let at = 0
      hunks.forEach((c, i) => {
        const lead = now.slice(at, c.from)
        const clipped = i === 0 ? (c.from > CONTEXT ? '…' + lead.slice(-CONTEXT) : lead) : (lead.length > CONTEXT * 2 ? lead.slice(0, CONTEXT) + ' … ' + lead.slice(-CONTEXT) : lead)
        pre.append(clipped)
        if (c.insert) pre.append(h('ins', { text: c.insert }))
        if (c.to > c.from) pre.append(h('del', { text: now.slice(c.from, c.to) }))
        at = c.to
      })
      if (hunks.length) {
        const tail = now.slice(at)
        pre.append(tail.length > CONTEXT ? tail.slice(0, CONTEXT) + '…' : tail)
      }
      body.push(pre)
    }
    return h('div', { class: 'dc-preview' },
      h('div', { class: 'dc-preview-h' }, h('span', { text: 'As it was then; marked where it differs from now' }), h('span', { class: 'dc-grow' }),
        st.can.edit && p.text != null ? h('button', { class: 'dc-sm ok', type: 'button', onclick: () => restore(s, head_) }, 'Restore this version') : null),
      ...body)
  }

  async function restore(s, head_) {
    st.preview = null
    const ans = await call(() => api.restore(s.id))
    if (!ans) return
    showToast('Restored', head_ ? () => call(() => api.restore(head_.id)).then(loadHistory) : null)
    await loadHistory()
  }

  function resolvedView() {
    const closed = st.notes.filter(n => n.state !== 'open').sort((a, b) => (b.closed?.at || '').localeCompare(a.closed?.at || ''))
    if (!closed.length) return [h('div', { class: 'dc-quiet', text: 'Nothing resolved yet.' })]
    return closed.map(n => h('div', { class: 'dc-ri' },
      h('div', { class: 'dc-ch' }, avatar(n.author, 20), h('span', { class: 'dc-nm', text: name(n.author) }), h('span', { class: 'dc-tm', text: rel(n.created) }), h('span', { class: 'dc-grow' }),
        n.state !== 'accepted' && (n.kind === 'comment' ? mayResolve(n) : st.can.accept) ? h('button', { class: 'dc-sm ghost', type: 'button', onclick: () => reopen(n) }, 'Reopen') : null),
      h('div', { class: 'dc-qt', text: clip(n.anchor.quote, 140) }),
      n.kind === 'suggestion' ? suggestionLine(n) : null,
      bodyText(n) ? h('p', { class: 'dc-tx', text: bodyText(n) }) : null,
      n.replies?.length ? h('button', { class: `dc-more dc-open-replies${st.openReplies.has(n.id) ? ' open' : ''}`, type: 'button', 'aria-expanded': String(st.openReplies.has(n.id)), onclick: () => { if (st.openReplies.has(n.id)) st.openReplies.delete(n.id); else st.openReplies.add(n.id); render() } }, icon('chevronRight', 12), n.replies.length === 1 ? '1 reply' : `${n.replies.length} replies`) : null,
      n.replies?.length && st.openReplies.has(n.id) ? h('div', { class: 'dc-rps' }, n.replies.map(r => h('div', { class: 'dc-rp' },
        avatar(r.author, 18),
        h('div', {},
          h('div', { class: 'dc-rh' }, h('b', { text: name(r.author) }), h('span', { class: 'dc-tm', title: stamp(r.created), text: ` ${rel(r.created)}` })),
          h('div', { class: 'dc-rt', text: r.text }))))) : null,
      h('div', { class: 'dc-stl', text: `${{ resolved: 'Resolved', accepted: 'Accepted', rejected: 'Rejected' }[n.state]}${n.closed?.by ? ` by ${name(n.closed.by)}` : ''} · ${rel(n.closed?.at)}` })))
  }

  function renderDrawer() {
    const which = st.drawer
    if (!which || st.phone) { drawer.classList.remove('on'); return }
    drawer.classList.add('on')
    put(drawer, 
      h('div', { class: 'dc-dh' }, h('h2', { text: which === 'history' ? 'History' : 'Resolved' }), h('button', { class: 'dc-ib', type: 'button', 'aria-label': 'Close', onclick: () => { st.drawer = null; render() } }, icon('x', 17))),
      h('div', { class: 'dc-dbody' }, which === 'history' ? historyView() : resolvedView()))
  }

  // -- Show authors ----------------------------------------------------------------------------------

  async function toggleAuthors() {
    st.authors = !st.authors
    settings?.set('authors', st.authors)
    st.menu = null
    if (st.authors) await loadAuthors()
    else view.dispatch({ effects: setAuthors.of([]), annotations: remote.of(true) })
    render()
  }

  async function loadAuthors() {
    try {
      const ans = st.history && st.history.revision === st.server.revision ? st.history : await run(() => api.history())
      st.history = ans
      for (const [id, p] of Object.entries(ans.people || {})) people[id] = { ...p, ...(people[id] || {}) }
      if (!st.authors) return
      const order = []
      for (const s of ans.authors || []) if (!order.includes(s.author)) order.push(s.author)
      const map = (p, assoc) => st.sinceBase.mapPos(p, assoc)
      // Spans are in the stored text; the record covers the doc only when it
      // has one. The frontmatter is never tinted.
      const spans = (ans.authors || []).map(s => ({ from: map(st.pm.toEditor(s.start), 1), to: map(st.pm.toEditor(s.end), -1), cls: `dc-au-${order.indexOf(s.author) % 6}` }))
      st.authorOrder = order
      view.dispatch({ effects: setAuthors.of(ans.revision === st.server.revision ? spans : []), annotations: remote.of(true) })
      render()
    } catch (err) {
      fail(err)
    }
  }

  function renderLegend() {
    const order = st.authors ? (st.authorOrder || []) : []
    legend.classList.toggle('on', st.authors && order.length > 0)
    put(legend, ...order.map((id, i) => h('span', { class: 'dc-lg' }, h('i', { class: `dc-au-dot dc-au-${i % 6}` }), people[id]?.name || id)))
  }

  // -- the phone: the bar above the keyboard and the sheet --------------------------------------------

  function renderBars() {
    if (!st.phone) { kbar.classList.remove('on'); vbar.classList.remove('on'); return }
    const focused = view.hasFocus
    const r = selectionRange()
    const writing = focused && canFormat()
    kbar.classList.toggle('on', writing && !st.sheet)
    vbar.classList.toggle('on', !writing && Boolean(r) && st.can.comment && !st.sheet)
    if (writing) {
      const a = active(view.state)
      const tools = st.mode === 'edit' ? [
        h('button', { class: 'dc-kb-aa', type: 'button', 'aria-label': 'Text style', onclick: () => openSheet('blocks') }, h('span', { text: 'Aa' }), icon('chevronDown', 12)),
        h('button', { class: a.bold ? 'on' : '', type: 'button', 'aria-label': 'Bold', onclick: () => toggleInline(view, 'bold') }, icon('bold', 19)),
        h('button', { class: a.italic ? 'on' : '', type: 'button', 'aria-label': 'Italic', onclick: () => toggleInline(view, 'italic') }, icon('italic', 19)),
        h('button', { class: a.link ? 'on' : '', type: 'button', 'aria-label': 'Link', onclick: () => { const url = prompt('Link address', linkAt(view.state, view.state.selection.main.from)); if (url !== null) setLink(view, url.trim()) } }, icon('link', 19)),
        r ? null : h('button', { class: a.block === 'ul' ? 'on' : '', type: 'button', 'aria-label': 'List', onclick: () => setBlock(view, a.block === 'ul' ? 'p' : 'ul') }, icon('list', 19)),
        r ? null : h('button', { class: a.block === 'task' ? 'on' : '', type: 'button', 'aria-label': 'Checklist', onclick: () => setBlock(view, a.block === 'task' ? 'p' : 'task') }, icon('checklist', 19)),
      ] : []
      put(kbar, 
        h('div', { class: 'dc-kr' }, tools),
        h('span', { class: 'dc-grow' }),
        tools.length ? h('span', { class: 'dc-ksep' }) : null,
        st.can.comment ? h('button', { class: 'dc-kc', type: 'button', disabled: r ? null : true, onclick: () => startNote('comment') }, icon('comment', 17), h('span', { text: 'Comment' })) : null,
        r && st.can.suggest ? h('button', { class: 'dc-kc', type: 'button', onclick: () => startNote('suggestion') }, icon('suggest', 17), h('span', { text: 'Suggest' })) : null,
        opts.barExtras?.() ? h('span', { class: 'dc-kmic' }, opts.barExtras()) : null,
      )
    }
    if (!writing && r) {
      put(vbar, h('button', { type: 'button', onclick: () => startNote('comment') }, icon('comment', 17), h('span', { text: 'Comment' })))
    }
  }

  function openSheet(kind) {
    st.sheet = kind
    render()
  }

  function closeSheet() {
    st.sheet = null
    if (st.compose) { st.compose = null; pushNotes() }
    if (st.drawer) st.drawer = null
    st.focus = null
    pushNotes()
    render()
  }

  sheetScrim.addEventListener('click', closeSheet)

  // The phone's thread sheet covers the lower part of the page: the passage it
  // is about is scrolled into what is left above it, with room made below the
  // text when the passage is near the end.
  function revealAboveSheet() {
    if (st.destroyed || !st.phone || st.sheet !== 'thread') return
    const r = currentNoteRanges().find(x => x.id === st.focus)
    if (!r || r.from == null) return
    const z = zoom()
    const sh = sheet.offsetHeight
    col.style.paddingBottom = `${sh + 48}px`
    const sr = scroll.getBoundingClientRect()
    const passageTop = (view.documentTop - sr.top + view.lineBlockAt(r.from).top) / z + scroll.scrollTop
    const passageEnd = (view.documentTop - sr.top + view.lineBlockAt(r.to).bottom) / z + scroll.scrollTop
    const sheetTop = (root.getBoundingClientRect().bottom - sr.top) / z - sh
    const top = scroll.scrollTop
    if (passageTop >= top + 12 && Math.min(passageEnd, passageTop + 90) <= top + sheetTop - 12) return
    const target = Math.max(0, passageTop - Math.max(16, (sheetTop - Math.min(passageEnd - passageTop, sheetTop * 0.6)) / 3))
    scroll.scrollTo({ top: target, behavior: 'smooth' })
  }

  function renderSheet() {
    const k = st.phone ? st.sheet : null
    sheetScrim.classList.toggle('on', Boolean(k))
    sheet.classList.toggle('on', Boolean(k))
    if (!k) return
    let body = []
    const close = h('button', { class: 'dc-ib dc-cls', type: 'button', 'aria-label': 'Close', onclick: closeSheet }, icon('x', 17))
    if (k === 'thread') {
      const n = st.notes.find(x => x.id === st.focus)
      if (!n) { st.sheet = null; return renderSheet() }
      body = [h('div', { class: 'dc-thr' },
        h('div', { class: 'dc-ch' }, avatar(n.author, 24), h('span', { class: 'dc-nm', text: name(n.author) }), h('span', { class: 'dc-tm', text: rel(n.created) }), h('span', { class: 'dc-grow' }),
          n.kind === 'comment' && mayResolve(n) ? h('button', { class: 'dc-sm ghost dc-resolve', type: 'button', onclick: () => resolve(n).then(closeSheet) }, icon('check', 14), ' Resolve') : null, close),
        ...threadBody(n, { full: true }))]
    } else if (k === 'compose' && st.compose) {
      body = [composeCard()]
    } else if (k === 'blocks') {
      const current = blockOf(view.state)
      body = [h('div', { class: 'dc-sh' }, h('b', { text: 'Text style' }), h('span', { class: 'dc-grow' }), close),
        h('div', { class: 'dc-bsh' }, (blockOf(view.state) === 'p' && !view.state.doc.lineAt(view.state.selection.main.head).text.trim() ? BLOCKS : turnable()).map(b => h('button', { class: `dc-bi${current === b.id ? ' on' : ''}`, type: 'button', onclick: () => { st.sheet = null; setBlock(view, b.id); render() } },
          h('span', { class: `dc-bt${b.glyph ? ' g' : ''}` }, b.icon ? icon(b.icon, 17) : b.glyph), h('span', { class: 'dc-bl', text: b.label }), h('span', { class: 'dc-bk', text: b.md.trim() }))))]
    } else if (k === 'history' || k === 'resolved') {
      body = [h('div', { class: 'dc-sh' }, h('b', { text: k === 'history' ? 'History' : 'Resolved' }), h('span', { class: 'dc-grow' }), close),
        h('div', { class: 'dc-sbody' }, k === 'history' ? historyView() : resolvedView())]
    }
    put(sheet, h('div', { class: 'dc-grab' }), ...body)
  }

  // -- toast -------------------------------------------------------------------------------------------

  let toastTimer = null
  function showToast(text, undo = null, bad = false, tick = false) {
    clearTimeout(toastTimer)
    toast.className = `dc-toast on${bad ? ' bad' : ''}${undo ? '' : ' plain'}`
    put(toast, 
      tick ? h('span', { class: 'dc-tk' }, icon('check', 13)) : null,
      h('span', { text }),
      undo ? h('button', { type: 'button', onclick: () => { toast.classList.remove('on'); undo() } }, 'Undo') : null,
    )
    toastTimer = setTimeout(() => toast.classList.remove('on'), bad ? 7000 : 5000)
  }

  // -- render ---------------------------------------------------------------------------------------------

  function render() {
    if (st.destroyed) return
    const bare = !st.phone && !st.compose && openNotes().length === 0
    root.className = `dc mode-${st.mode}${st.phone ? ' phone' : ' desk'}${bare ? ' bare' : ''}${st.authors ? ' authors' : ''}${st.reattach ? ' reattaching' : ''}${st.drawer && !st.phone ? ' drawer-open' : ''}`
    renderHead()
    banner.classList.toggle('on', Boolean(st.reattach))
    if (st.reattach) {
      put(banner, icon('reattach', 15), h('span', { text: 'Select the words this note is about, then choose Attach.' }),
        h('button', { class: 'dc-sm ok', type: 'button', onclick: finishReattach }, 'Attach'), h('button', { class: 'dc-sm ghost', type: 'button', onclick: () => { st.reattach = null; render() } }, 'Cancel'))
    }
    renderMargin()
    renderDrawer()
    renderSheet()
    if (st.phone && st.sheet === 'thread' && st.focus) {
      if (st.sheetFor !== st.focus) { st.sheetFor = st.focus; requestAnimationFrame(revealAboveSheet) }
    } else if (st.sheetFor) { st.sheetFor = null; col.style.paddingBottom = '' }
    renderBars()
    renderLegend()
    if (st.mode !== 'view' || !st.focus || st.phone) qpop.classList.remove('on')
    onState?.(st)
  }

  // -- public -----------------------------------------------------------------------------------------------

  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => queueLayout()) : null
  ro?.observe(col)

  applyAnswer(opts.doc)
  watch()
  if (st.mode === 'suggest') startSuggesting()
  if (st.authors) loadAuthors()

  return {
    dom: root,
    view,
    state: st,
    flush,
    setPhone(phone) {
      if (st.phone === Boolean(phone)) return
      st.phone = Boolean(phone)
      st.sheet = null
      hideSelbar()
      render()
    },
    setSlots({ take: t = null, player: p = null } = {}) {
      if (t !== take.firstChild) put(take, ...(t ? [t] : []))
      if (p !== player.firstChild) put(player, ...(p ? [p] : []))
    },
    refreshHead: () => renderHead(),
    // A fresh read of the doc (focus came back, the file changed on disk).
    reload: () => reloadQuietly(),
    // The text as the editor holds it; replacing it is an edit like typing.
    // In editor space, with editor positions (what dictation's field reads).
    text: () => view.state.doc.toString(),
    replaceText(next, caret = null) {
      const cur = view.state.doc.toString()
      const cs = ChangeSet.of(diffChanges(cur, next), cur.length, '\n')
      view.dispatch({ changes: cs, selection: caret == null ? undefined : EditorSelection.cursor(Math.min(caret, next.length)), userEvent: 'input.dictation' })
    },
    dirty: () => !st.sinceBase.empty || st.saving,
    destroy() {
      st.destroyed = true
      clearTimeout(saveTimer)
      clearTimeout(suggestTimer)
      clearTimeout(watchTimer)
      ro?.disconnect()
      view.destroy()
    },
  }
}
