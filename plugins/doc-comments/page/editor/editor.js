// The calm editor: one Markdown doc, its notes in the margin, three ways of
// working (Editing, Suggesting, Viewing), formatting that appears only when it
// is wanted, History and authors behind ⋯.
//
// It is a standalone component. Inside Loam it is mounted by page/main.js as
// Files' viewer for Markdown; in step 2 the same component is the outsider's
// page. Everything it needs from its host comes in through `createDocEditor`'s
// options: the operations (`api`, the plugin server's routes), faces, links,
// embeds, the breadcrumb, and whatever the host puts in the header or bar
// (read aloud). Dictation's mic comes in apart (`mic`), because it is drawn
// only while the doc takes typed text from this person (`canType`): never in
// Viewing, never on a doc that cannot be written. `onCanType(yes)` tells the
// host when that changes, so a take running into the doc can be stopped.

import {
  EditorState, EditorView, EditorSelection, ChangeSet, Compartment, Transaction, Annotation, Prec, ViewPlugin,
  keymap, history, historyKeymap, defaultKeymap, drawSelection, indentMore, indentLess, syntaxTree,
} from '../vendor/codemirror.js'
import { h, icon, rel, stamp, put, tone, TONE_INK } from './dom.js'
import { changes as diffChanges, posMap, editorText, suggestionShape, tokens as tokenize } from './textdiff.js'
import {
  markdownLanguage, inline, blocks, notesField, setNotes, authorsField, setAuthors,
  modeField, setMode, suggestBase, setSuggestBase, focusField, setFocused,
} from './live.js'
import { remote, draftsField, setDrafts, setRecording, inBase, applied, passages, draftDecos } from './record.js'
import { INLINE, BLOCKS, active, toggleInline, setLink, linkAt, setBlock, blockOf } from './format.js'
import { createToc } from './toc.js'

export const AUTOSAVE_MS = 1200
export const WATCH_MS = 1500
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
  return h('span', { class: 'dc-face', style: { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.48)}px`, background: tone(name), color: TONE_INK }, text: name.charAt(0).toUpperCase() })
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
    fileMenu = null, onText = null, onState = null, projection = null,
  } = opts
  // The editor's working text is "editor space" (textdiff.js, posMap): a file
  // whose lines all end in CRLF is held with one position per line break.
  // What is sent is always the file's own text (sliceDoc), and every position
  // to or from the server goes through `st.pm`.
  const visible = t => projection?.show(t) ?? t
  const opening = visible(opts.doc.text)
  const CRLF = lineSeparatorOf(opening) === '\r\n'
  const ed = t => editorText(t, CRLF)
  const raw = t => CRLF ? t.replace(/\r?\n/g, '\r\n') : t
  function positions(text) {
    if (!projection) return posMap(text, CRLF)
    const { fromSource, toSource } = projection.offsets(text)
    const source = posMap(text, false)
    return {
      toEditor: offset => fromSource(source.toEditor(offset)),
      toServer: (offset, assoc) => source.toServer(toSource(offset, assoc)),
      slice: (from, to) => text.slice(toSource(from, 1), toSource(to, -1)),
    }
  }
  const st = {
    server: { text: opts.doc.text, revision: opts.doc.revision },
    pm: positions(opts.doc.text),
    mapBase: ed(opening),
    sinceBase: ChangeSet.empty(ed(opening).length),
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
    // The selection's tools are wanted (desktop: the popup) or offered (a
    // phone's bars) only once the selecting has stopped.
    selWant: false, barSel: false,
    // Suggesting: my own open suggestions, kept as real text in the editor
    // (record.js); `pending` while typing waits for its pause.
    pending: false,
  }
  if (!opts.doc.can?.edit && st.mode !== 'view') st.mode = opts.doc.can?.suggest ? 'suggest' : 'view'
  let saveTimer = null, suggestTimer = null, layoutQueued = false
  // The text moves aside for the margin (or back to the middle) only between
  // keystrokes: `typedAt` is the last key in the text, `shownBare` what the
  // page shows, `bareTimer` the redraw that waits for the pause.
  let typedAt = 0, shownBare = null, bareTimer = null
  const TYPING_PAUSE = 900
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
  const notice = h('div', { class: 'dc-notice' })
  col.append(notice, legend, gutter, plus)
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

  // My drafts drawn over the editor's text: the original words struck, mine
  // in the accent, highlighted with their card.
  const draftsDrawn = ViewPlugin.fromClass(class {
    constructor(v) { this.decorations = this.build(v.state) }
    update(u) {
      if (u.docChanged || u.startState.field(draftsField) !== u.state.field(draftsField) || u.transactions.some(t => t.effects.length)) this.decorations = this.build(u.state)
    }
    build(state) {
      const rec = state.field(draftsField)
      return draftDecos(rec.list, (d) => d.ids[0] ? { id: d.ids[0], on: st.focus === d.ids[0], peek: st.peek === d.ids[0] } : null, state.doc)
    }
  }, { decorations: (v) => v.decorations })

  const view = new EditorView({
    parent: col,
    state: EditorState.create({
      doc: opening,
      selection: { anchor: bodyStart(opening) },
      extensions: [
        EditorState.lineSeparator.of(lineSeparatorOf(opening)),
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
        notesField,             // before the blocks: a table marks the cells notes sit in
        blocks(embed),
        authorsField,
        suggestBase,
        draftsField,
        draftsDrawn,
        EditorView.contentAttributes.of({ spellcheck: 'true', autocorrect: 'on', autocapitalize: 'sentences', 'aria-label': 'Document' }),
        EditorView.updateListener.of(onUpdate),
        EditorView.domEventHandlers({
          mousedown: onEditorMouseDown,
          click: onEditorClick,
          mouseover: onEditorOver,
          mouseout: onEditorOut,
          keydown: () => { typedAt = Date.now() },
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

  // The table of contents (toc.js): ticks at the text's left on a desktop,
  // the section in the bar's title on a phone.
  const docTitle = () => opts.titleBar?.title || (opts.crumbs || []).at(-1)?.label || ''
  const toc = createToc({
    view, root, scroll, col, zoom, local,
    phone: () => st.phone,
    title: docTitle,
    openList: () => openSheet('toc'),
    picked: (where) => { if (where === 'sheet' && st.sheet === 'toc') closeSheet() },
    onActive: () => { renderHead(); if (st.sheet === 'toc') renderSheet() },
  })

  function editableExt() {
    const writable = st.mode === 'view' ? false : st.mode === 'suggest' ? Boolean(st.can.suggest) : Boolean(st.can.edit)
    return [EditorView.editable.of(writable), EditorState.readOnly.of(!writable)]
  }

  function canFormat() {
    return (st.mode === 'edit' && st.can.edit) || (st.mode === 'suggest' && st.can.suggest)
  }

  // Whether the doc takes typed text from this person just now: Editing or
  // Suggesting, with the right to. It is what CodeMirror is told
  // (`editableExt`), and it is the rule for dictation's mic (`opts.mic`).
  function canType() { return canFormat() }

  // -- the server's answers ----------------------------------------------------------

  function notePassage(from, to, replacement) {
    const start = st.pm.toServer(from, 1)
    const end = Math.max(start, st.pm.toServer(to, -1))
    if (!projection || replacement == null) return {
      start, end, quote: st.pm.slice(from, to),
      ...(replacement == null ? {} : { replacement: raw(replacement) }),
    }
    const base = st.server.text
    const visibleBase = st.mapBase
    const changed = projection.write(base, visibleBase.slice(0, from) + replacement + visibleBase.slice(to))
    const source = posMap(base, false)
    // Preserve the original selection where possible, widening only when the
    // proposed edit changes an adjacent marker or creates a new board item.
    const selectedStart = source.toEditor(start), selectedEnd = source.toEditor(end)
    let first = 0, tail = 0
    while (first < selectedStart && first < changed.length && base[first] === changed[first]) first++
    while (tail < base.length - selectedEnd && tail < changed.length - first
      && base[base.length - tail - 1] === changed[changed.length - tail - 1]) tail++
    const last = base.length - tail
    return {
      start: source.toServer(first), end: source.toServer(last), quote: base.slice(first, last),
      replacement: changed.slice(first, changed.length - (base.length - last)),
    }
  }

  function suggestionText(note) {
    const replacement = note.replacement || ''
    const start = note.where?.start, end = note.where?.end
    if (!projection || start == null || end == null) return ed(replacement)
    const base = st.server.text, source = posMap(base, false)
    const a = source.toEditor(start), b = source.toEditor(end)
    const suggested = ed(visible(base.slice(0, a) + replacement + base.slice(b)))
    const from = st.pm.toEditor(start), tail = st.mapBase.length - st.pm.toEditor(end)
    return suggested.slice(from, suggested.length - tail)
  }

  function people_(author) {
    if (!author) return { name: 'Someone', kind: 'owner' }
    return { ...author, ...(people[author.id] || {}) }
  }

  // Every answer carries the doc's text as stored. What the person typed since
  // the text the editor's changes are counted from is kept on top of it, and
  // the notes' passages are mapped into the editor's text the same way.
  function applyAnswer(ans) {
    if (st.destroyed || !ans) return
    st.lastAns = ans
    const T = ans.text
    const ET = ed(visible(T))
    let userOver, editorUnchanged = false
    if (view.state.field(draftsField).on) {
      ;({ userOver, editorUnchanged } = rebaseDrafts(ans, ET))
    } else try {
      const serverCS = ChangeSet.of(diffChanges(st.mapBase, ET), st.mapBase.length, '\n')
      // Where both put words at one place, the stored text's come first (a
      // first note's id line at the top goes above what was typed there).
      userOver = st.sinceBase.map(serverCS)
      const forEditor = serverCS.map(st.sinceBase, true)
      editorUnchanged = forEditor.empty
      if (!forEditor.empty) {
        const keep = scrollAnchor()
        // A caret where the stored text gained words (a first note's id line
        // at the very top) stays after them, with the words it was typed among.
        view.dispatch({ changes: forEditor, selection: view.state.selection.map(forEditor, 1), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
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
    if ('notes_revision' in ans) st.notesRev = ans.notes_revision
    st.pm = positions(T)
    st.mapBase = ET
    st.sinceBase = userOver
    st.id = ans.id
    st.me = ans.me || st.me
    st.can = ans.can || st.can
    st.resolved = ans.resolved || 0
    if (view.state.field(draftsField).on && adoptReturned(ans.notes || [])) editorUnchanged = false
    // Positions from the server are clamped to the text: a closed note's
    // stored place can lie past a text that has since grown shorter, and it
    // must never stop a doc from opening.
    const len = ET.length
    const clamp = (p) => Math.max(0, Math.min(len, p))
    const map = (p, assoc) => (p == null ? p : userOver.mapPos(clamp(st.pm.toEditor(p)), assoc))
    const mine = draftById()
    st.notes = (ans.notes || []).map(n => {
      const w = n.where || {}
      // A draft's passage is its own words in the editor.
      const d = mine.get(n.id)
      if (d) return { ...n, from: d.from, to: Math.max(d.from, d.to) }
      if (n.state !== 'open') return { ...n, from: null, to: null }
      const from = w.start == null ? null : map(w.start, 1)
      const to = w.end == null ? null : Math.max(from ?? 0, map(w.end, -1))
      return { ...n, from, to }
    })
    for (const n of st.notes) for (const a of [n.author, ...(n.replies || []).map(r => r.author)]) if (a?.id && !people[a.id]) people[a.id] = a
    if (st.focus && !st.notes.some(n => n.id === st.focus && n.state === 'open')) st.focus = null
    st.dirty = !st.sinceBase.empty && st.mode === 'edit'
    // An answer that changes nothing (the watch reading a doc whose notes'
    // file was only rewritten) draws nothing.
    const sig = JSON.stringify([ans.revision, ans.notes, ans.resolved, ans.can, [...mine.keys()]])
    if (sig === st.lastSig && editorUnchanged) return
    st.lastSig = sig
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
          // The doc's text, or its notes (a note, reply or close from anyone).
          let ans
          try { ans = await api.revision() } catch (err) {
            // A share page whose link was turned off (or whose session ended):
            // stop asking, and say so in place of the doc.
            if ((err?.status === 404 || err?.status === 401 || err?.status === 410) && opts.onGone) { watching = false; st.gone = true; opts.onGone(err); return }
            throw err
          }
          const notesMoved = ans && 'notes' in ans && st.notesRev !== undefined && ans.notes !== st.notesRev
          if (ans && 'notes' in ans) st.notesRev = ans.notes
          if (ans?.revision && (ans.revision !== st.server.revision || notesMoved) && !st.saving) await reloadQuietly()
        } catch { /* the next look tries again */ }
        watching = false
      }
      watch()
    }, WATCH_MS)
  }

  function pushNotes() {
    const draftIds = new Set(draftById().keys())
    const list = []
    for (const n of st.notes) {
      if (n.state !== 'open' || n.from == null) continue
      list.push({ id: n.id, kind: draftIds.has(n.id) ? 'draft' : n.kind, from: n.from, to: n.to, replacement: projection ? suggestionText(n) : n.replacement, focus: st.focus === n.id, peek: st.peek === n.id, changed: n.where?.state === 'changed' })
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
    const msg = ours ? plainError(err) : 'Something went wrong here, so the doc has been read again.'
    if (!ours) {
      console.error('doc-comments:', err)
      // read again, at most every ten seconds: nothing may loop
      const now = Date.now()
      if (!st.lastRescue || now - st.lastRescue > 10000) {
        st.lastRescue = now
        setTimeout(() => { if (!st.destroyed) reloadQuietly() }, 0)
      }
    }
    st.error = msg
    showToast(msg, null, true)
    render()
  }

  // The server's sentences, said for a person (and the doc read again when
  // what was asked about is no longer there).
  function plainError(err) {
    const m = String(err?.message || '')
    if (/^no (note|reply) '/i.test(m) || err.status === 404 && /note|reply/i.test(m)) {
      setTimeout(() => { if (!st.destroyed) reloadQuietly() }, 0)
      return 'That note is no longer on this doc.'
    }
    if (/is empty$/i.test(m)) return 'Write something first.'
    if (/already (resolved|accepted|rejected)/i.test(m)) return `That was ${m.split('already ')[1]} already.`
    if (err.status === 0) return 'Comments could not reach their server. Your words are kept here.'
    if (err.status >= 500) return 'The comments server did not answer. Try again in a moment.'
    return m ? m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.') : 'That did not work.'
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
      if (view.state.field(draftsField).on) st.sinceBase = draftsCS()
      else for (const t of u.transactions) if (t.docChanged) st.sinceBase = st.sinceBase.compose(t.changes)
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
    if (u.docChanged) toc.changed(true)
    if (u.selectionSet) selectionMoved(u)
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
    keepFinalNewline()
    const text = projection?.write(st.server.text, view.state.sliceDoc()) ?? view.state.sliceDoc()
    const body = { base_revision: st.server.revision, base_text: st.server.text, text }
    // The edit as the editor made it, in the stored text's positions: the
    // server carries every note through it exactly (a diff of the two texts
    // cannot tell which of two equal words was typed).
    if (!projection && st.mapBase === ed(st.server.text)) {
      const changes = []
      st.sinceBase.iterChanges((fromA, toA, _fb, _tb, ins) => changes.push([st.pm.toServer(fromA), st.pm.toServer(toA), raw(ins.toString())]))
      body.changes = changes
    }
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
      st.mapBase = ed(visible(st.server.text))
      st.sinceBase = ChangeSet.of(diffChanges(st.mapBase, view.state.doc.toString()), st.mapBase.length, '\n')
      st.word = 'Couldn’t save'
      st.error = err?.message || String(err)
      renderSaveWord()
      clearTimeout(saveTimer)
      saveTimer = setTimeout(() => save(), 8000)
      throw err
    }
  }

  // A file that ended with a line break keeps it, and a new (empty) one gets
  // one, even when the last words were typed on its last, empty line.
  function keepFinalNewline(force = false) {
    const was = visible(st.server.text)
    if (was !== '' && !/\n$/.test(was)) return
    const doc = view.state.doc
    // Not while the caret is on the last line being typed: added under it
    // there, a "/" typed and deleted again left one line break more. It is
    // added when the doc is let go (flush), or once the caret moves off.
    if (!force && view.hasFocus && doc.lineAt(view.state.selection.main.head).number === doc.lines) return
    if (!doc.length || /\n$/.test(doc.sliceString(Math.max(0, doc.length - 1)))) return
    const sel = view.state.selection
    view.dispatch({ changes: { from: doc.length, insert: view.state.lineBreak }, selection: sel, annotations: Transaction.addToHistory.of(false) })
  }

  async function flushNow() {
    clearTimeout(saveTimer)
    if (st.mode === 'edit') keepFinalNewline(true)
    if (st.mode === 'edit' && !st.sinceBase.empty) await saveNow()
    if (st.mode === 'suggest') await suggestNow()
  }

  function flush() {
    return run(flushNow).catch(fail)
  }

  // -- suggesting -------------------------------------------------------------------------

  function scheduleSuggest() {
    clearTimeout(suggestTimer)
    st.pending = true
    suggestTimer = setTimeout(() => run(suggestNow).catch(fail), AUTOSAVE_MS * 1.5)
  }

  function drafts() { return view.state.field(draftsField).list }

  // My drafts by the server note each grows.
  function draftById() {
    const m = new Map()
    for (const d of drafts()) if (d.ids[0]) m.set(d.ids[0], d)
    return m
  }

  // The drafts as a change of the stored text into the editor's.
  function draftsCS(base = st.mapBase) {
    return ChangeSet.of(inBase(drafts(), view.state.doc).map(r => ({ from: r.start, to: r.end, insert: r.text })), base.length, '\n')
  }

  // An answer while Suggesting (drafts.js gave way to record.js, 2026-11-17).
  // First the drafts closed by anyone: an accepted one's words are now in the
  // stored text, so the editor does not change (and its caret does not move);
  // a rejected one's words go back to the original. Then the stored text's own
  // changes are mapped around the open drafts, which keep their words and place.
  // Whether a change removed the whole of [a, b] with text on both sides of
  // it gone too (a point strictly inside a removed run, or a range wholly in
  // one): a draft there has lost its passage.
  function removedAround(cs, a, b) {
    if (!cs) return false
    let gone = false
    cs.iterChanges((fA, tA) => { if (tA > fA && fA < a && b < tA) gone = true })
    return gone
  }

  function rebaseDrafts(ans, ET) {
    const open = new Set((ans.notes || []).filter(n => n.state === 'open').map(n => n.id))
    const states = new Map((ans.notes || []).map(n => [n.id, n.state]))
    let base = st.mapBase
    const rs = inBase(drafts(), view.state.doc)
    const keep = [], back = []
    let acc = '', at = 0
    // A draft whose original words were removed from the file (on disk,
    // by someone else) has nowhere to stand: it is set aside (its typed
    // words leave the editor with the removed text) and never sent again as
    // a suggestion on other words. Its note, if sent, waits detached.
    const whereOf = new Map((ans.notes || []).map(n => [n.id, n.where?.state]))
    let pre = null
    try { pre = ChangeSet.of(diffChanges(base, ET), base.length, '\n') } catch { pre = null }
    let lost = 0
    for (const r of rs) {
      const id = r.d.ids[0]
      const gone = id ? whereOf.get(id) === 'detached' : removedAround(pre, r.start, r.end)
      if (gone) { lost++; if (id) (st.lostIds ||= new Set()).add(id); back.push({ from: r.d.from, to: r.d.to, insert: r.d.orig }); continue }
      if (!id || open.has(id)) { keep.push(r.d); continue }
      if (states.get(id) === 'accepted' && r.d.sent != null) {
        // Its words as last sent are in the stored text now.
        acc += base.slice(at, r.start) + r.d.sent
        at = r.end
        if (r.text !== r.d.sent) keep.push({ ...r.d, orig: r.d.sent, ids: [], sent: null })
      } else {
        back.push({ from: r.d.from, to: r.d.to, insert: r.d.orig })
      }
    }
    base = acc + base.slice(at)
    if (lost) showToast(lost === 1 ? 'A suggestion of yours was on words just removed from the file. It waits in ⋯ History, detached.' : `${lost} suggestions of yours were on words just removed from the file. They wait in ⋯ History, detached.`)
    if (lost) st.lostToast = true
    if (back.length) {
      view.dispatch({ changes: back, effects: setDrafts.of({ list: keep }), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
    } else if (keep.length !== rs.length) {
      view.dispatch({ effects: setDrafts.of({ list: keep }), annotations: remote.of(true) })
    }
    // The open drafts' places in the stored text as it was (with accepts).
    const before = inBase(drafts(), view.state.doc)
    let editorUnchanged = true
    if (base !== ET) {
      const serverCS = ChangeSet.of(diffChanges(base, ET), base.length, '\n')
      const ownCS = ChangeSet.of(before.map(r => ({ from: r.start, to: r.end, insert: r.text })), base.length, '\n')
      const forEditor = serverCS.map(ownCS, true)
      // Words the stored text changed inside a draft's original words become
      // its original words (the draft's own words stay as typed).
      const origs = new Map(before.map(r => {
        const a = serverCS.mapPos(r.start, 1), b = Math.max(a, serverCS.mapPos(r.end, -1))
        return [r.d.key, ET.slice(a, b)]
      }))
      if (!forEditor.empty) {
        editorUnchanged = false
        const keepScroll = scrollAnchor()
        view.dispatch({ changes: forEditor, selection: view.state.selection.map(forEditor, 1), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
        if (keepScroll) holdScroll(keepScroll, forEditor)
      }
      view.dispatch({ effects: setDrafts.of({ list: drafts().map(d => ({ ...d, orig: origs.has(d.key) ? origs.get(d.key) : d.orig })) }), annotations: remote.of(true) })
    }
    // The invariant, checked: the stored text with my drafts applied is the
    // editor's text. If ever not, the drafts are dropped and made again from
    // the server's notes, rather than shown wrong.
    if (applied(ET, drafts(), view.state.doc) !== view.state.doc.toString()) {
      console.error('doc-comments: drafts out of step with the stored text; reading them again')
      const now = view.state.doc.toString()
      view.dispatch({ changes: ChangeSet.of(diffChanges(now, ET), now.length, '\n'), effects: setDrafts.of({ list: [], gone: [] }), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
      st.mapBase = ET
      adoptDrafts(ans.notes || [], ET)
      editorUnchanged = false
    }
    return { userOver: draftsCS(ET), editorUnchanged: editorUnchanged && !back.length }
  }

  // A pause sends my drafts as they are: each one changed since it was last
  // sent goes as the suggestion it now is (growing its note: `supersedes`),
  // drafts the person joined take one note (the others withdrawn), and a
  // draft typed and deleted again is withdrawn. Nothing in the editor changes.
  async function suggestNow() {
    clearTimeout(suggestTimer)
    st.pending = false
    if (st.destroyed || !view.state.field(draftsField).on) return
    const gone = view.state.field(draftsField).gone
    if (gone.length) view.dispatch({ effects: setDrafts.of({ gone: [] }), annotations: remote.of(true) })
    for (const id of gone) {
      try { applyAnswer(await api.withdraw(id)) } catch (err) { if (err?.status !== 404) fail(err) }
    }
    const done = new Set()                       // each draft at most once a pause
    for (let guard = 0; guard < 200; guard++) {
      // Each draft as its passage now; one whose passage the server does not
      // hold as such (new, grown, or moved by a neighbour) is sent.
      const notes = new Map(st.notes.map(n => [n.id, n]))
      const all = passages(st.mapBase, drafts(), view.state.doc)
      const next = all.find(({ r, p }) => {
        if (done.has(r.d.key)) return false
        if (!p) return r.d.sent !== r.text
        const n = notes.get(r.d.ids[0])
        if (!n || n.state !== 'open' || r.d.sent !== r.text) return true
        const proposed = notePassage(p.start, p.end, p.replacement)
        return n.where?.start !== proposed.start || n.where?.end !== proposed.end || (n.replacement || '') !== proposed.replacement
      })
      if (!next) break
      const { r, p } = next
      const key = r.d.key, text = r.text
      done.add(key)
      const mark = (patch) => view.dispatch({ effects: setDrafts.of({ list: drafts().map(d => (d.key === key ? { ...d, ...patch } : d)) }), annotations: remote.of(true) })
      if (!p) { mark({ sent: text }); continue }          // nothing to anchor on yet
      const body = {
        kind: 'suggestion', text: '', revision: st.server.revision,
        ...notePassage(p.start, p.end, p.replacement),
      }
      if (r.d.ids[0]) body.supersedes = r.d.ids[0]
      try {
        const ans = await api.add(body)
        const extra = r.d.ids.slice(1)
        mark({ ids: [ans.note], sent: text })
        applyAnswer(ans)
        for (const id of extra) {
          try { applyAnswer(await api.withdraw(id)) } catch (err) { if (err?.status !== 404) fail(err) }
        }
      } catch (err) {
        // The words stay in the editor; the next pause tries again.
        fail(err)
        break
      }
    }
  }

  // My open suggestions as drafts: their words put into the editor's text,
  // only the part that differs (the passage's anchoring words are not).
  function adoptDrafts(notes, base) {
    const mineOpen = mineAsDrafts(notes, base)
    const take = []
    for (const m of mineOpen) if (!take.length || m.start >= take[take.length - 1].end) take.push(m)
    const changes = take.map(m => ({ from: m.start, to: m.end, insert: m.text }))
    let shift = 0
    const list = take.map(m => {
      const from = m.start + shift
      shift += m.text.length - m.orig.length
      return { key: `n:${m.id}`, from, to: from + m.text.length, orig: m.orig, ids: [m.id], sent: m.text }
    })
    // Built against the text after the change: set the list after it.
    const cs = ChangeSet.of(changes, view.state.doc.length, '\n')
    view.dispatch({ changes: cs, selection: view.state.selection.map(cs, -1), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
    view.dispatch({ effects: setDrafts.of({ list, gone: [] }), annotations: remote.of(true) })
  }

  // While Suggesting, a suggestion of mine that is attached again (its words
  // came back to the file, after it was set aside) becomes a draft again,
  // where nothing of mine already is; and the line that said it was set
  // aside goes. Returns whether any came back.
  function adoptReturned(notes) {
    if (!view.state.field(draftsField).on) return false
    const have = draftById()
    const now = inBase(drafts(), view.state.doc)
    const clear = (m) => now.every(r => m.end < r.start || m.start > r.end)
    const take = []
    for (const m of mineAsDrafts(notes, st.mapBase)) {
      if (have.has(m.id) || !clear(m)) continue
      if (take.length && m.start <= take[take.length - 1].end) continue
      take.push(m)
    }
    if (!take.length) return false
    const toEd = draftsCS()
    const changes = take.map(m => ({ from: toEd.mapPos(m.start, 1), to: toEd.mapPos(m.end, -1), insert: m.text }))
    const cs = ChangeSet.of(changes, view.state.doc.length, '\n')
    view.dispatch({ changes: cs, selection: view.state.selection.map(cs, -1), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
    let shift = 0
    const added = take.map((m, i) => {
      const from = changes[i].from + shift
      shift += m.text.length - (changes[i].to - changes[i].from)
      return { key: `n:${m.id}`, from, to: from + m.text.length, orig: m.orig, ids: [m.id], sent: m.text }
    })
    const list = [...drafts(), ...added].sort((x, y) => x.from - y.from)
    view.dispatch({ effects: setDrafts.of({ list }), annotations: remote.of(true) })
    st.sinceBase = draftsCS()
    if (st.lostIds && take.filter(m => st.lostIds.delete(m.id)).length && !st.lostIds.size && st.lostToast) {
      toast.classList.remove('on')
      st.lostToast = false
    }
    return true
  }

  // My open, attached suggestions as drafts over ``base``: their words put
  // into the editor's text, only the part that differs (the passage's
  // anchoring words are not).
  function mineAsDrafts(notes, base) {
    return notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author?.id === st.me?.id && n.where?.state === 'attached' && n.where.start != null)
      .map(n => {
        const s = Math.max(0, Math.min(base.length, st.pm.toEditor(n.where.start))), e = Math.max(s, Math.min(base.length, st.pm.toEditor(n.where.end)))
        const quote = base.slice(s, e), rep = suggestionText(n)
        // Whole words shared at either end are left out, never letters: a
        // suggestion of "forty-three" for "forty-one" is "one" → "three", not
        // "on" → "thre" with an "e" between.
        const A = tokenize(quote), B = tokenize(rep)
        let pt = 0
        while (pt < A.length && pt < B.length && A[pt] === B[pt]) pt++
        let qt = 0
        while (qt < A.length - pt && qt < B.length - pt && A[A.length - 1 - qt] === B[B.length - 1 - qt]) qt++
        const p = A.slice(0, pt).join('').length
        const q = A.slice(A.length - qt).join('').length
        return { id: n.id, start: s + p, end: e - q, orig: quote.slice(p, quote.length - q), text: rep.slice(p, rep.length - q) }
      })
      .sort((a, b) => a.start - b.start)
  }

  // Into Suggesting: recording on, my open suggestions become drafts. Out of
  // it: their words go back, and they are drawn over the stored text again.
  function enterDrafts() {
    view.dispatch({ effects: setRecording.of(true), annotations: remote.of(true) })
    adoptDrafts(st.notes, st.mapBase)
    st.sinceBase = draftsCS()
  }

  function redrawPlaces() {
    st.lastSig = null
    if (st.lastAns) applyAnswer(st.lastAns)
    else pushNotes()
  }

  function leaveDrafts() {
    st.pending = false
    const back = drafts().map(d => ({ from: d.from, to: d.to, insert: d.orig }))
    view.dispatch({ changes: back, effects: setRecording.of(false), annotations: [remote.of(true), Transaction.addToHistory.of(false)] })
    st.sinceBase = ChangeSet.empty(st.mapBase.length)
  }

  // -- modes -------------------------------------------------------------------------------

  async function setModeTo(mode) {
    if (mode === st.mode) return
    if ((mode === 'edit' && !st.can.edit) || (mode === 'suggest' && !st.can.suggest)) return
    await run(flushNow).catch(fail)
    const was = st.mode
    st.mode = mode
    if (was === 'suggest') leaveDrafts()
    view.dispatch({ effects: [setMode.of(mode), editableC.reconfigure(editableExt()), setSuggestBase.of(mode === 'suggest' ? st.mapBase : null), historyC.reconfigure(history())], annotations: remote.of(true) })
    if (mode === 'suggest') enterDrafts()
    // The notes' places were worked out over the text as it was drawn: the
    // drafts' words just came in (or went out), so they are worked out again.
    redrawPlaces()
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
        Object.assign(body, notePassage(from, to, body.replacement))
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
      return api.reattach(note.id, { ...notePassage(from, to), revision: st.server.revision })
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
    // Phone: a tap on a note's words opens its sheet and does not also put
    // the caret there (inside a list item it would show the raw "- ").
    if (st.phone && !(e.metaKey || e.ctrlKey)) {
      const id = noteAt(e.target)
      if (id && id !== '__new') { e.preventDefault(); focusNote(id); return true }
    }
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
      // (a mark in a drawn table cannot move the selection: a click there is
      // always for its note)
      if (sel.empty || st.mode === 'view' || st.phone || e.target.closest('.dc-tablewrap')) focusNote(id)
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

  // It waits until the selecting stops: never there while a selection is
  // being made, so it cannot flicker over the pointer or the words being
  // chosen. After a mouse or trackpad drag it comes a beat after the button
  // is up (longer after a double-click, which may yet become a triple); after
  // Shift+arrows, Select All or anything else, once the selection has been
  // still for a moment. On a phone the same wait holds the keyboard bar's
  // Comment and Suggest, and the slim bar, until the fingers are up and the
  // handles still. Any new selecting sends it away at once.
  const SETTLE = { release: 120, double: 300, still: 350 }
  const gest = { mouse: false, touch: false, touchedAt: 0, timer: 0, at: null, release: SETTLE.release }

  function settleLater(ms) {
    clearTimeout(gest.timer)
    gest.timer = setTimeout(settled, ms)
  }

  function settled() {
    if (st.destroyed || gest.mouse || gest.touch) return
    if (st.phone) { if (!st.barSel) { st.barSel = true; renderBars() } return }
    st.selWant = true
    renderSelbar()
  }

  // A selection changed. The popup's own commands (formatting, a link) and
  // the server's answers keep it where it is, redrawn; anything the person
  // does sends it away until the selection settles again.
  function selectionMoved(u) {
    const own = u.transactions.every(t => !t.selection || t.annotation(remote) || t.isUserEvent('input.format'))
    if (own) {
      if (!st.phone && (st.selWant || st.link)) queueSelbar()
      return
    }
    if (st.phone) {
      // Handles dragged on a selection already offered: the bar stays as it
      // is. A caret, or a new selection, waits.
      if (!selectionRange()) { clearTimeout(gest.timer); st.barSel = false; return }
      if (!st.barSel && !gest.touch && !gest.mouse) settleLater(SETTLE.still)
      return
    }
    if (st.link) return
    dropSelbar()
    // The pointer's last word on a gesture can come just after the button is
    // up; it keeps the gesture's own wait.
    const pointer = u.transactions.some(t => t.isUserEvent('select.pointer'))
    if (!gest.mouse && selectionRange()) settleLater(pointer ? gest.release : SETTLE.still)
  }

  // The pointer goes down in the text: whatever was offered leaves now, and
  // nothing comes until the button is up (a narrow window with a mouse has
  // the phone's bars, and they wait for the button too).
  function onSelectStart(e) {
    // The mouse events a browser makes up after a touch are the touch's.
    if (e.button !== 0 || gest.touch || Date.now() - gest.touchedAt < 800) return
    gest.mouse = true
    clearTimeout(gest.timer)
    if (!e.target.closest?.('.dc-selbar')) dropSelbar()
  }
  function onSelectEnd(e) {
    if (!gest.mouse) return
    gest.mouse = false
    gest.at = { x: e.clientX, y: e.clientY }
    gest.release = e.detail === 2 ? SETTLE.double : SETTLE.release
    if (st.link || !selectionRange() || (st.phone && st.barSel)) return
    settleLater(gest.release)
  }
  function onTouchStart() {
    gest.touch = true
    gest.touchedAt = Date.now()
    clearTimeout(gest.timer)
  }
  function onTouchEnd(e) {
    if (!gest.touch || e.touches?.length) return
    gest.touch = false
    gest.touchedAt = Date.now()
    if (!selectionRange() || st.link) return
    // A phone's bar, or a touch screen's popup: once the handles are still.
    if (st.phone ? !st.barSel : !st.selWant) settleLater(SETTLE.still)
  }
  view.dom.addEventListener('mousedown', onSelectStart, true)
  view.dom.addEventListener('touchstart', onTouchStart, { capture: true, passive: true })
  globalThis.addEventListener?.('mouseup', onSelectEnd, true)
  globalThis.addEventListener?.('touchend', onTouchEnd, true)
  globalThis.addEventListener?.('touchcancel', onTouchEnd, true)

  let selbarQueued = false
  function queueSelbar() {
    if (selbarQueued) return
    selbarQueued = true
    requestAnimationFrame(() => { selbarQueued = false; renderSelbar() })
  }

  // Sent away by the person: gone until the selection settles again.
  function dropSelbar() {
    clearTimeout(gest.timer)
    st.selWant = false
    hideSelbar()
  }

  let selbarOut = 0
  function hideSelbar({ keep = false } = {}) {
    if (!keep) st.selWant = false
    st.link = null
    if (!selbar.classList.contains('on')) return
    // A very quick fade, already out of the pointer's way.
    selbar.classList.remove('on', 'in')
    selbar.classList.add('out')
    clearTimeout(selbarOut)
    selbarOut = setTimeout(() => selbar.classList.remove('out'), 90)
  }

  function showSelbar(below) {
    clearTimeout(selbarOut)
    const entering = !selbar.classList.contains('on')
    selbar.classList.remove('out')
    selbar.classList.toggle('blw', below)
    selbar.classList.add('on')
    if (entering) {
      // Restart the entrance even when a fade out was still running.
      selbar.classList.remove('in')
      void selbar.offsetWidth
      selbar.classList.add('in')
    }
  }
  selbar.addEventListener('animationend', (e) => { if (e.target === selbar) selbar.classList.remove('in') })

  // The part of the selection on the screen, between the header and the
  // bottom of the scroller, or null when none of it shows. A long selection
  // (Select All) is placed by the part in sight: its far ends are not drawn.
  function selectionInView(from, to) {
    const sr = scroll.getBoundingClientRect(), cr = view.contentDOM.getBoundingClientRect()
    const top = Math.max(sr.top, head.getBoundingClientRect().bottom), bottom = sr.bottom
    if (bottom - top < 8) return null
    const x = Math.max(cr.left + 1, Math.min(cr.left + 40, cr.right - 1))
    const p0 = view.posAtCoords({ x, y: top + 2 }, false), p1 = view.posAtCoords({ x, y: bottom - 2 }, false)
    const lo = view.lineBlockAt(p0).from, hi = view.lineBlockAt(p1).to
    if (to <= lo || from >= hi) return null
    const f = Math.max(from, lo), t = Math.min(to, hi)
    const ca = view.coordsAtPos(f, 1), cb = view.coordsAtPos(t, -1)
    if (!ca || !cb || cb.bottom <= top || ca.top >= bottom) return null
    return { from: f, to: t }
  }

  function tip(label, md, key) {
    return h('span', { class: 'dc-tip' }, h('span', { text: label }), md ? h('code', { text: md }) : null, key ? h('kbd', { text: key.replace('Mod', MOD).replace(/ /g, ' ') }) : null)
  }

  function renderSelbar() {
    if (st.phone || st.destroyed) return hideSelbar()
    const r = selectionRange()
    const focused = view.hasFocus
    if (st.link) return renderLinkBar()
    if (!st.selWant || gest.mouse) return hideSelbar({ keep: true })
    if (!r || !focused || st.compose) return hideSelbar()
    // Scrolled out of sight: it goes, and comes back with the words.
    const seen = selectionInView(r.from, r.to)
    if (!seen) return hideSelbar({ keep: true })
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
    selbar.classList.toggle('fmt', st.mode === 'edit' && st.can.edit && !st.reattach)
    selbar.classList.remove('lnk')
    put(selbar, ...items)
    showSelbar(placeAbove(selbar, seen.from, seen.to))
  }

  // Returns whether it went below the selection.
  function placeAbove(el, from, to) {
    const ca = view.coordsAtPos(from), cb = view.coordsAtPos(to, -1)
    if (!ca || !cb) return false
    const a = local(ca), b = local(cb), c = local(col.getBoundingClientRect())
    const box = rootSize()
    const was = el.classList.contains('on')
    el.style.visibility = 'hidden'
    el.classList.add('on')
    const w = el.offsetWidth, hgt = el.offsetHeight
    if (!was) el.classList.remove('on')
    el.style.visibility = ''
    const sameLine = Math.abs(a.top - b.top) < 4
    const mid = sameLine ? (a.left + b.right) / 2 : (a.left + Math.min(b.right, c.right)) / 2
    let left = mid - w / 2
    left = Math.max(8, Math.min(left, box.width - w - 8))
    // Just above the first line of the selection, clear of the header;
    // below its last line when there is no room above.
    let top = a.top - hgt - 8
    let below = top < head.offsetHeight + 4
    if (below) top = b.bottom + 8
    // A selection taller than the screen: at the top of the view.
    const vb = local(scroll.getBoundingClientRect()).bottom
    if (below && top + hgt > vb - 8) { top = head.offsetHeight + 8; below = false }
    // Never under the pointer where the button came up: the other side of
    // the selection when that side has room.
    const p = gest.at && local({ left: gest.at.x, right: gest.at.x, top: gest.at.y, bottom: gest.at.y })
    if (p && p.left > left - 6 && p.left < left + w + 6 && p.top > top - 6 && p.top < top + hgt + 6) {
      const alt = below ? a.top - hgt - 8 : b.bottom + 8
      if (below ? alt >= head.offsetHeight + 4 : alt + hgt <= box.height - 8) { top = alt; below = !below }
    }
    el.style.left = `${left}px`
    el.style.top = `${top}px`
    return below
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
    selbar.classList.remove('fmt')
    selbar.classList.add('lnk')
    put(selbar, h('span', { class: 'dc-lk' }, icon('link', 15)), input, h('button', { class: 'dc-fb ok primary', type: 'button', 'aria-label': 'Apply link', onclick: done }, icon('check', 14)))
    showSelbar(placeAbove(selbar, l.from, l.to))
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
    const c = view.coordsAtPos(line.from, 1)
    let top
    if (c) { const l = local(c, col); top = (l.top + l.bottom) / 2 - 13 }
    else { const block = view.lineBlockAt(line.from), z = zoom(); top = block.top / z + (block.height / z - 26) / 2 + 40 }
    plus.style.marginTop = '0'
    plus.style.top = `${top}px`
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
    if (st.bmenu && !st.phone) col.style.paddingBottom = ''
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
    bmenu.style.visibility = ''
    bmenu.classList.add('on')
    const hgt = bmenu.scrollHeight
    const floor = head.offsetHeight + 6
    let roomBelow = box.height - 12 - below
    // Not enough room below the caret: the doc scrolls up to make it, as
    // long as the caret line stays in view under the header.
    if (hgt > roomBelow && b.at !== 'selbar') {
      const need = Math.ceil(hgt - roomBelow)
      let canScroll = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop
      // Near the end of the doc (where blocks get added) there is room to
      // make below the last line for as long as the menu is open.
      if (above - need > floor + 8 && canScroll < need) {
        col.style.paddingBottom = `${parseFloat(getComputedStyle(col).paddingBottom) + need - canScroll + 8}px`
        canScroll = need
      }
      if (above - need > floor + 8 && canScroll >= need) {
        // After CodeMirror's own measuring of the keystroke (which would undo
        // a scroll made now); the scroll places the menu again, below.
        const target = scroll.scrollTop + need
        requestAnimationFrame(() => requestAnimationFrame(() => { if (!st.bmenu) return; scroll.scrollTop = Math.max(scroll.scrollTop, target); renderBmenu() }))
        below -= need; above -= need; roomBelow += need
        bmenu.style.visibility = 'hidden'
      }
    }
    const roomAbove = above - floor
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
      viaLink(n.author) ? h('span', { class: 'dc-via', title: 'Came in through a share link', text: n.author.kind === 'guest-agent' ? 'agent · via link' : 'via link' }) : null,
      isNew(n) ? h('span', { class: 'dc-new', title: 'New since you last opened this doc' }) : null,
      focused ? h('span', { class: 'dc-tm', title: stamp(n.created), text: rel(n.created) }) : null,
      changed ? h('span', { class: 'dc-chg', title: `Text changed since. It read: “${n.anchor.quote}”` }, icon('changed', 14)) : null,
      h('span', { class: 'dc-grow' }),
      focused && mine(n) && !st.editing ? ownTools(n) : null,
      focused && isComment && mayResolve(n) ? h('button', { class: 'dc-ib dc-resolve', type: 'button', title: 'Resolve', 'aria-label': 'Resolve', onclick: (e) => { e.stopPropagation(); resolve(n) } }, icon('check', 15)) : null,
    )
  }

  // Someone outside Loam, shown so to Loam's own people (not to visitors,
  // who know it already).
  function viaLink(a) {
    return /^guest/.test(a?.kind || '') && /^(owner|agent)$/.test(st.me?.kind || '')
  }

  function isNew(n) {
    const seen = opts.doc.seen
    if (!seen) return false
    const other = (a) => a?.id !== st.me?.id
    return (n.created > seen && other(n.author)) || (n.replies || []).some(r => r.created > seen && other(r.author))
  }

  // One's own words: the note's (from its head) and each reply's.
  function mine(item) {
    return Boolean(item?.author?.id && st.me?.id && item.author.id === st.me.id)
  }

  function ownTools(n, r = null) {
    return h('span', { class: 'dc-own' },
      h('button', { class: 'dc-ib', type: 'button', title: 'Change your words', 'aria-label': 'Change your words', onclick: (e) => { e.stopPropagation(); st.editing = { id: n.id, rid: r ? r.id : null }; render(); setTimeout(() => (st.phone ? sheet : root).querySelector('.dc-edit-fld textarea')?.focus({ preventScroll: true }), 30) } }, icon('pencil', 13)),
      r ? h('button', { class: 'dc-ib', type: 'button', title: 'Remove your reply', 'aria-label': 'Remove your reply', onclick: (e) => { e.stopPropagation(); removeReply(n, r) } }, icon('trash', 13))
        : n.kind === 'suggestion'
          // One's own suggestion is withdrawn, said in a word, in sight.
          ? h('button', { class: 'dc-sm ghost dc-withdraw', type: 'button', onclick: (e) => { e.stopPropagation(); removeNote(n) } }, 'Withdraw')
          : h('button', { class: 'dc-ib', type: 'button', title: 'Delete your note', 'aria-label': 'Delete your note', onclick: (e) => { e.stopPropagation(); removeNote(n) } }, icon('trash', 13)))
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
        h('button', { class: 'dc-sm ok primary', type: 'button', onclick: (e) => { e.stopPropagation(); done(true) } }, 'Save')))
  }

  // Removing one's own words keeps an Undo: the reply is posted again.
  async function removeReply(n, r) {
    const ans = await call(() => api.deleteReply(n.id, r.id))
    if (ans) showToast('Reply removed', () => call(() => api.reply(n.id, r.text)))
  }

  // One's own note goes with its replies only when nobody else has replied
  // (the server checks); a suggestion of one's own is withdrawn.
  async function removeNote(n) {
    const w = n.where || {}
    const ans = await call(() => (n.kind === 'suggestion' ? api.withdraw(n.id) : api.removeNote(n.id)))
    if (ans) {
      if (st.sheet === 'thread') closeSheet()
      st.focus = null
      // Undo puts it back: the same words on the same passage, and one's
      // own replies after it.
      const again = async () => {
        const back = await call(() => api.add({ kind: n.kind, text: n.text || '', start: w.start, end: w.end, quote: n.anchor?.quote, revision: st.server.revision, ...(n.kind === 'suggestion' ? { replacement: n.replacement } : {}) }))
        for (const r of n.replies || []) if (back?.note) await call(() => api.reply(back.note, r.text))
      }
      showToast(n.kind === 'suggestion' ? 'Suggestion withdrawn' : 'Note deleted', w.start != null ? again : null)
      render()
    }
  }

  function mayResolve(n) {
    const a = n.author?.id || '', me = st.me?.id || ''
    // one's own, or (a visitor) one's own agent's
    return st.can.resolve_any || a === me || (me.startsWith('guest:') && a === `guest-agent:${me.slice(6)}`)
  }

  // What a suggestion does, in words: an insertion says what it adds, a
  // deletion what it takes out, anything else what the passage becomes.
  function suggestionLine(n) {
    const from = projection && n.where?.start != null && n.where?.end != null
      ? st.mapBase.slice(st.pm.toEditor(n.where.start), st.pm.toEditor(n.where.end))
      : visible(n.anchor?.quote ?? '')
    const replacement = projection ? suggestionText(n) : (n.replacement ?? '')
    const shape = suggestionShape(from, replacement)
    // Paragraphs made, joined, added or taken away are said as such: a
    // line break quoted is nothing to read ("Replace with “”").
    const gone = from.slice(shape.from, shape.to), came = shape.insert
    const para = /\n[ \t]*\n/
    if (shape.kind === 'insert' && !came.trim() && /\n/.test(came)) return h('div', { class: 'dc-stl' }, h('span', { text: 'Split the paragraph' }))
    if (shape.kind === 'delete' && !gone.trim() && /\n/.test(gone)) return h('div', { class: 'dc-stl' }, h('span', { text: 'Join two paragraphs' }))
    if (shape.kind === 'insert' && para.test(came)) return h('div', { class: 'dc-stl' }, h('span', { text: 'Add a paragraph ' }), h('ins', { text: `“${clip(came)}”` }))
    if (shape.kind === 'delete' && para.test(gone)) return h('div', { class: 'dc-stl' }, h('span', { text: 'Delete a paragraph ' }), h('del', { text: `“${clip(gone)}”` }))
    // A space turned into a line break, or a line break into a space: the
    // paragraph is split or joined, and no space is struck.
    if (!from.trim() && !replacement.trim() && from !== replacement) {
      const nlA = /\n/.test(from), nlB = /\n/.test(replacement)
      if (nlB && !nlA) return h('div', { class: 'dc-stl' }, h('span', { text: 'Split the paragraph' }))
      if (nlA && !nlB) return h('div', { class: 'dc-stl' }, h('span', { text: 'Join two paragraphs' }))
    }
    if (shape.kind === 'replace' && !replacement.trim()) return h('div', { class: 'dc-stl' }, h('span', { text: 'Delete ' }), h('del', { text: `“${clip(from)}”` }))
    if (shape.kind === 'insert') return h('div', { class: 'dc-stl' }, h('span', { text: 'Add ' }), h('ins', { text: `“${clip(shape.insert)}”` }))
    if (shape.kind === 'delete') return h('div', { class: 'dc-stl' }, h('span', { text: 'Delete ' }), h('del', { text: `“${clip(from.slice(shape.from, shape.to))}”` }))
    return h('div', { class: 'dc-stl' }, h('span', { text: 'Replace with ' }), h('ins', { text: `“${clip(replacement)}”` }))
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
    const send = h('button', { class: 'dc-send primary', type: 'button', 'aria-label': 'Send reply', onclick: async () => { const t = input.value; input.value = ''; wrap.classList.remove('has'); await reply(n, t) } }, icon('send', 15))
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
    if (full && n.where?.state === 'changed') {
      // What the passage says now, in quotation marks of its own (not a
      // style's): "It now reads “lixght”".
      const now = (!projection ? n.now : null) ?? (n.from != null ? view.state.sliceDoc(n.from, n.to) : '')
      out.push(h('div', { class: 'dc-chgl' }, 'The words changed since. It now reads ', h('span', { class: 'dc-chgq', text: `“${clip(now, 120)}”` })))
    }
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
        h('button', { class: 'dc-sm ok primary', type: 'button', disabled: n.where?.state !== 'attached' ? true : null, title: n.where?.state === 'changed' ? 'The words changed since this was suggested' : null, onclick: (e) => { e.stopPropagation(); accept(n) } }, 'Accept')))
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
    const btn = h('button', { class: 'dc-sm ok primary', type: 'button', disabled: c.busy || (c.kind === 'comment' && !c.text.trim()) ? true : null, onclick: submitCompose }, c.kind === 'suggestion' ? 'Suggest' : 'Comment')
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
  // Where a note's own drawn words begin (its first mark or struck words):
  // a deletion that wraps, or a deleted paragraph, starts where it is drawn,
  // not on the line after it.
  function drawnTop(id) {
    if (!id) return null
    const el = view.contentDOM.querySelector(`[data-note="${CSS.escape(id)}"]`)
    const r = el?.getClientRects?.()[0]
    if (!r) return null
    if (el.matches('del, ins')) return r
    const node = document.createTreeWalker(el, NodeFilter.SHOW_TEXT).nextNode()
    if (!node) return r
    const range = document.createRange()
    range.setStart(node, 0); range.setEnd(node, Math.min(1, node.length))
    return range.getClientRects()[0] || r
  }

  function anchorTop(pos, id = null) {
    const z = zoom()
    const pageTop = page.getBoundingClientRect().top
    const d = drawnTop(id)
    if (d) return (d.top - pageTop) / z
    const c = view.coordsAtPos(pos, 1)
    if (c) return (c.top - pageTop) / z
    return (view.documentTop - pageTop + view.lineBlockAt(pos).top) / z + 4
  }

  function layout() {
    fitHead()
    if (st.destroyed || !root.isConnected) return
    const ranges = new Map(currentNoteRanges().map(n => [n.id, n]))
    if (!st.phone && st.mode !== 'view') {
      const items = []
      for (const [id, el] of cardEls) {
        const r = ranges.get(id)
        if (!r || r.from == null) continue
        // The card's head row (below its 10 px of padding) level with the
        // first line of its passage.
        items.push({ id, el, want: anchorTop(r.from, id) - 10, hgt: el.offsetHeight, at: r.from })
      }
      // Reading order: by line, and along a line by place, never by when made.
      items.sort((a, b) => (Math.abs(a.want - b.want) < 6 ? a.at - b.at : a.want - b.want))
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
      // Never taller than the text by more than a card's room: a card placed
      // from a guessed height must not leave pages of empty scroll.
      margin.style.minHeight = `${Math.ceil(Math.min(lowest + 40, col.offsetHeight + 240))}px`
      // A thread just opened is brought wholly into view, its reply field too.
      const want = st.compose ? '__new' : st.focus
      const it = items.find(x => x.id === want)
      if (it && st.revealFor !== want) {
        st.revealFor = want
        const z = zoom()
        const sr = scroll.getBoundingClientRect()
        const top = (page.getBoundingClientRect().top - sr.top) / z + scroll.scrollTop + it.top
        const bottom = top + it.hgt
        const view0 = scroll.scrollTop, viewH = scroll.clientHeight
        let to = null
        if (bottom > view0 + viewH - 16) to = Math.min(top - 16, bottom - viewH + 24)
        else if (top < view0 + 8) to = top - 16
        if (to != null) scroll.scrollTo({ top: Math.max(0, to), behavior: 'smooth' })
      }
      if (!want) st.revealFor = null
    } else margin.style.minHeight = ''
    renderGutter(ranges)
    toc.changed(false)
    renderPlus()
    if ((selbar.classList.contains('on') || st.selWant) && !st.link) renderSelbar()
    if (st.mode === 'view' && st.focus && !st.phone) placeQpop()
  }

  // Viewing (desktop) and the phone: small marks in the gutter, one per line
  // with notes; a click opens the thread.
  // A mark sits centred on the line its passage starts on (the visual line,
  // not the paragraph's first), measured where the line is drawn.
  function markTop(pos, size, id = null) {
    const c = drawnTop(id) || view.coordsAtPos(pos, 1)
    if (c) {
      const l = local(c, gutter)
      return (l.top + l.bottom) / 2 - size / 2
    }
    const block = view.lineBlockAt(pos)
    const z = zoom()
    return (block.top + Math.min(block.height, 30 * z) / 2) / z - size / 2
  }

  let gutterKey = ''
  function renderGutter(ranges) {
    const show = st.phone || st.mode === 'view'
    if (!show) { if (gutterKey) { put(gutter); gutterKey = '' } return }
    const byLine = new Map()
    for (const n of openNotes()) {
      const r = ranges.get(n.id)
      if (!r || r.from == null) continue
      const top = Math.round(markTop(r.from, 24, n.id))
      if (!byLine.has(top)) byLine.set(top, [])
      byLine.get(top).push(n)
    }
    // Drawn again only when a mark moved or changed (layout runs often).
    const key = JSON.stringify([st.phone, st.focus, [...byLine].map(([t, ns]) => [t, ns.map(n => n.id)])])
    if (key === gutterKey) return
    gutterKey = key
    const marks = []
    for (const [top, ns] of byLine) {
      const on = ns.some(n => n.id === st.focus)
      const m = h('button', { class: `dc-mk${on ? ' on' : ''}`, type: 'button', 'aria-label': `${ns.length} note${ns.length > 1 ? 's' : ''}`, style: { top: `${top}px` }, onclick: () => focusNote(on ? null : ns[0].id) },
        st.phone ? null : avatar(ns[0].author, 18), !st.phone && ns.length > 1 ? h('span', { text: String(ns.length) }) : null)
      marks.push(m)
    }
    put(gutter, ...marks)
    // Centred by the mark's own height: Loam's page gives buttons a larger
    // touch target on a phone than the 24 px the mark is drawn for.
    for (const m of marks) {
      const hgt = m.offsetHeight
      if (hgt && hgt !== 24) m.style.top = `${parseFloat(m.style.top) - (hgt - 24) / 2}px`
    }
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
    return h('div', { class: 'dc-menu', role: 'menu' }, MODES.filter(m => m.id === 'view' || (m.id === 'edit' ? st.can.edit : st.can.suggest)).map(m => {
      const allowed = true
      return h('button', { class: `dc-mi top${st.mode === m.id ? ' on' : ''}`, type: 'button', role: 'menuitemradio', 'aria-checked': String(st.mode === m.id), disabled: allowed ? null : true, onclick: () => setModeTo(m.id) },
        icon(m.icon, 17), h('span', { class: 'dc-l' }, h('b', { text: m.label }), h('small', { text: m.hint })), h('span', { class: 'dc-ck' }, icon('check', 16)))
    }))
  }

  function moreMenu() {
    const det = detached().length
    return h('div', { class: 'dc-menu', role: 'menu' },
      det && opts.history !== false ? h('button', { class: 'dc-mi dc-mi-lost', type: 'button', role: 'menuitem', onclick: () => openDrawer('history', { detached: true }) },
        icon('reattach', 17), h('span', { class: 'dc-l' }, h('b', { text: det === 1 ? '1 note lost its passage' : `${det} notes lost their passages` }), h('small', { text: 'Attach again, or read what it was about' })), h('span', { class: 'dc-chv' }, icon('chevronRight', 14))) : null,
      det && opts.history !== false ? h('div', { class: 'dc-msep' }) : null,
      // History and Show authors read the doc's history, which is the
      // owner's: a share page has neither (opts.history === false).
      opts.history === false ? null : h('button', { class: `dc-mi${st.authors ? ' on' : ''}`, type: 'button', role: 'menuitemcheckbox', 'aria-checked': String(st.authors), onclick: toggleAuthors },
        icon('people', 17), h('span', { class: 'dc-l' }, h('b', { text: 'Show authors' })), h('span', { class: `dc-sw${st.authors ? ' on' : ''}` })),
      opts.history === false ? null : h('button', { class: 'dc-mi', type: 'button', role: 'menuitem', onclick: () => openDrawer('history') },
        icon('history', 17), h('span', { class: 'dc-l' }, h('b', { text: 'History' })), det && opts.history === false ? h('span', { class: 'dc-ct', text: `${det} detached` }) : null, h('span', { class: 'dc-chv' }, icon('chevronRight', 14))),
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

  // The header is drawn again only when something in it changed: Loam's page
  // re-renders the viewer often (every stream event), and a header rebuilt
  // under the pointer once let a click on ⋯ land on a detached button.
  let headKey = null, headNodes = []
  function renderHead() {
    const mic = canType() && opts.micInHead?.() ? opts.mic?.() || null : null
    const extras = [...(opts.headExtras?.() || []), ...(mic ? [mic] : []), null, ...(opts.headEnd?.() || [])]
    const sw = saveWordNode()
    const key = JSON.stringify([st.mode, st.menu, st.phone, sw?.textContent || '', sw?.className || '', (opts.crumbs || []).map(c => c.label), opts.titleBar || null, opts.back?.label || null,
      st.authors, st.resolved, detached().length, st.can, (opts.menuExtras?.() || []).map(x => x.label), Boolean(fileMenu), st.phone && toc.active()])
    const cls = `dc-head${scroll.scrollTop > 4 ? ' scd' : ''}${head.classList.contains('tight') ? ' tight' : ''}`
    if (head.className !== cls) head.className = cls
    // A host's own buttons may be made afresh on every call (Share is):
    // the same markup is the same button.
    const same = (n, o) => n === o || (n && o && n.outerHTML === o.outerHTML)
    if (key === headKey && extras.length === headNodes.length && extras.every((n, i) => same(n, headNodes[i])) && head.firstChild) { fitHead(); return }
    headKey = key
    headNodes = extras
    const m = MODES.find(x => x.id === st.mode)
    // The phone names the doc (its folders are one tap away, behind the back
    // button); the desktop shows the whole path (shortened by the host).
    const all = opts.crumbs || []
    // With contents, the phone's title is the section being read (toc.js).
    const secTitle = st.phone ? toc.titleNode() : null
    const crumbs = st.phone && all.length
      ? [secTitle || h('b', { class: 'dc-docname', text: all[all.length - 1].label })]
      : all.flatMap((c, i) => [
        i ? h('span', { class: 'dc-sl', text: '/' }) : null,
        c.go ? h('button', { class: 'dc-crumb-b', type: 'button', onclick: c.go, text: c.label }) : h('span', { text: c.label }),
      ])
    const pill = h('button', { class: 'dc-mpill', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': String(st.menu === 'mode'), 'aria-label': `Mode: ${m.label}`, onclick: () => toggleMenu('mode') },
      icon(m.icon, 15), h('span', { text: m.label }), h('span', { class: 'dc-cv' }, icon('chevronDown', 12)))
    // A note whose passage is gone puts a small quiet mark on ⋯ (no banner).
    const lost = detached().length
    const dots = h('button', { class: `dc-gbtn${lost ? ' dc-lost' : ''}`, type: 'button', 'aria-label': lost ? `More (${lost} note${lost > 1 ? 's' : ''} lost ${lost > 1 ? 'their passages' : 'its passage'})` : 'More', title: 'More', 'aria-haspopup': 'menu', onclick: () => toggleMenu('more') }, icon('dots', 18))
    put(head, 
      opts.back ? h('button', { class: 'dc-gbtn dc-back', type: 'button', 'aria-label': opts.back.label, title: opts.back.label, onclick: opts.back.go }, icon('chevronLeft', 20)) : null,
      opts.titleBar
        ? h('div', { class: 'dc-crumb dc-titlebar' }, secTitle || h('b', { text: opts.titleBar.title }), opts.titleBar.sub ? h('span', { class: 'dc-sl', text: '·' }) : null, opts.titleBar.sub ? h('span', { text: opts.titleBar.sub }) : null)
        : h('div', { class: 'dc-crumb' }, crumbs),
      h('span', { class: 'dc-saveslot' }, sw),
      ...extras.slice(0, extras.indexOf(null)),
      // Only Viewing allowed: a quiet word, no menu.
      !st.can.edit && !st.can.suggest ? h('span', { class: 'dc-mpill dc-mpill-only' }, icon('eye', 15), h('span', { text: 'Viewing' })) : menuWrap('mode', pill, modeMenu()),
      menuWrap('more', dots, moreMenu()),
      ...extras.slice(extras.indexOf(null) + 1),
    )
    fitHead()
  }

  // On a phone the title is never cut while the mode pill can give way: it
  // shows its icon alone (its name is still its label) when the title needs
  // the room.
  function fitHead() {
    // (Only when it is there: `remove` rewrites the class even when it is
    // absent, and an idle page must change nothing.)
    // With contents, the title is the section being read, whose length
    // changes as the doc scrolls: the pill is always compact then, so the
    // bar does not shift at each section.
    if (st.phone && head.isConnected && head.querySelector('.dc-secbtn')) {
      if (!head.classList.contains('tight')) head.classList.add('tight')
      return
    }
    if (head.classList.contains('tight')) head.classList.remove('tight')
    if (!st.phone || !head.isConnected) return
    const t = head.querySelector('.dc-crumb > b')
    if (t && t.scrollWidth > t.clientWidth + 0.5) head.classList.add('tight')
  }

  scroll.addEventListener('scroll', () => {
    head.classList.toggle('scd', scroll.scrollTop > 4)
    if (!st.phone && (selbar.classList.contains('on') || st.selWant)) {
      if (st.link) renderLinkBar(); else renderSelbar()
    }
    if (st.bmenu) renderBmenu()
    if (st.mode === 'view' && st.focus && !st.phone) placeQpop()
  }, { passive: true })

  // On the document: after a click in a drawer, focus is often the page's
  // body, outside the editor.
  const onEscape = (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented || !root.isConnected) return
    if (st.drawer && !st.phone) { e.preventDefault(); st.drawer = null; st.preview = null; render() }
    else if (st.menu) { e.preventDefault(); st.menu = null; render() }
  }
  globalThis.document?.addEventListener('keydown', onEscape)

  root.addEventListener('mousedown', (e) => {
    if (st.menu && !e.target.closest('.dc-ddw')) { st.menu = null; render() }
    if (st.bmenu && !e.target.closest('.dc-bmenu,.dc-plus,.dc-fb.aa,.dc-kb-aa')) closeBmenu()
    if (e.target.closest('.dc-selbar,.dc-bmenu,.dc-plus,.dc-kbar,.dc-vbar') && !e.target.closest('input')) e.preventDefault()
    if (st.mode === 'view' && st.focus && !st.phone && !e.target.closest('.dc-qpop,.dc-mk,[data-note]')) { st.focus = null; pushNotes(); render() }
  })

  // -- drawers: History and Resolved ----------------------------------------------------------------

  async function openDrawer(which, { detached: toDetached = false } = {}) {
    st.menu = null
    st.drawer = which
    if (st.phone) openSheet(which)
    render()
    if (which === 'history') await loadHistory()
    if (toDetached) requestAnimationFrame(() => root.querySelector('.dc-drawer.on .dc-dsec, .dc-sheet.on .dc-dsec')?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
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
    const where = sum.section ? `“${clip(plain(sum.section), 44)}”` : sum.first ? `“${clip(plain(sum.first), 40)}”` : ''
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

  // A version is read as words, not Markdown: no heading marks or emphasis.
  function plain(t) {
    return String(t || '').replace(/^\s*(```|~~~)[^\n]*$/gm, '').replace(/!\[\[([^\]|]+)(\|[^\]]*)?\]\]/g, '[picture: $1]').replace(/`([^`\n]*)`/g, '$1').replace(/^#{1,6}\s*/, '').replace(/(^|\n)#{1,6} /g, '$1').replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2').replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2').replace(/\[\[([^\]]+)\]\]/g, '$1')
  }

  async function togglePreview(s) {
    if (st.preview?.id === s.id) { st.preview = null; render(); return }
    st.preview = { id: s.id, text: null }
    render()
    try {
      const ans = await api.version(s.id)
      if (st.preview?.id === s.id) {
        st.preview = { id: s.id, text: ans.text }
        render()
        // brought into view: an entry low in the list opened below the fold
        requestAnimationFrame(() => root.querySelector('.dc-preview')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
      }
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
      // Line by line first (changes at both ends of a doc are two changes,
      // not the whole doc), then word by word inside the changed lines; the
      // frontmatter is left out, and unchanged lines between become "…".
      const body_ = (t) => t.replace(/^---\r?\n[\s\S]*?\r?\n---[ \t]*\r?\n?/, '')
      const groups = lineChanges(body_(visible(st.server.text)), body_(visible(p.text)))
      const pre = h('pre')
      if (!groups.some(g => g.kind !== 'same')) pre.append('The same as now.')
      else groups.forEach((g, i) => {
        if (g.kind === 'same') {
          const lines = g.now
          const first = i === 0, last = i === groups.length - 1
          const keep = lines.length <= 4 ? lines : first ? ['…', ...lines.slice(-2)] : last ? [...lines.slice(0, 2), '…'] : [...lines.slice(0, 2), '…', ...lines.slice(-2)]
          pre.append(plain(keep.join('\n')) + '\n')
          return
        }
        const a = g.now.join('\n'), b = g.then.join('\n')
        let at = 0
        for (const c of diffChanges(a, b)) {
          pre.append(plain(a.slice(at, c.from)))
          if (c.insert) pre.append(h('ins', { text: plain(c.insert) }))
          if (c.to > c.from) pre.append(h('del', { text: plain(a.slice(c.from, c.to)) }))
          at = c.to
        }
        pre.append(plain(a.slice(at)) + '\n')
      })
      body.push(pre)
    }
    return h('div', { class: 'dc-preview' },
      h('div', { class: 'dc-preview-h', text: 'As it was then, marked where it differs from now' }),
      ...body,
      st.can.restore && p.text != null ? h('div', { class: 'dc-dbtns' }, h('button', { class: 'dc-sm ok primary', type: 'button', onclick: () => restore(s, head_) }, 'Restore this version')) : null)
  }

  // Groups of lines: unchanged, or changed between now and then.
  function lineChanges(now, then) {
    const A = now.split('\n'), B = then.split('\n')
    if (A.length * B.length > 4_000_000) return [{ kind: 'changed', now: A, then: B }]
    const m = A.length, n = B.length, w = n + 1
    const t = new Uint32Array((m + 1) * w)
    for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) t[i * w + j] = A[i] === B[j] ? t[(i + 1) * w + j + 1] + 1 : Math.max(t[(i + 1) * w + j], t[i * w + j + 1])
    const out = []
    const push = (kind, a, b) => {
      const last = out[out.length - 1]
      if (last && last.kind === kind) { if (a != null) last.now.push(a); if (b != null && kind !== 'same') last.then.push(b) }
      else out.push({ kind, now: a != null ? [a] : [], then: b != null && kind !== 'same' ? [b] : [] })
    }
    let i = 0, j = 0
    while (i < m && j < n) {
      if (A[i] === B[j]) { push('same', A[i]); i++; j++ }
      else if (t[(i + 1) * w + j] >= t[i * w + j + 1]) { push('changed', A[i], null); i++ }
      else { push('changed', null, B[j]); j++ }
    }
    while (i < m) push('changed', A[i++], null)
    while (j < n) push('changed', null, B[j++])
    return out
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
    if (!st.phone) { kbar.classList.remove('on'); vbar.classList.remove('on'); if (kbar.firstChild) put(kbar); return }
    const focused = view.hasFocus
    // A selection is offered once it has settled (see `settled`).
    const r = st.barSel ? selectionRange() : null
    const writing = focused && canFormat()
    // Forced booleans: `toggle(name, undefined)` flips the class instead,
    // which left an empty bar at load and two bars after a blur.
    kbar.classList.toggle('on', Boolean((writing || st.klink) && !st.sheet))
    vbar.classList.toggle('on', Boolean(!writing && !st.klink && r && st.can.comment && !st.sheet))
    if (st.klink && st.phone) {
      // The link, typed in the bar itself, where the keyboard already is.
      const l = st.klink
      if (kbar.classList.contains('dc-klink') && kbar.querySelector('input')) return
      const input = h('input', { type: 'url', inputmode: 'url', placeholder: 'Paste or type a link', value: l.url, 'aria-label': 'Link address', enterkeyhint: 'done' })
      const done = (apply) => {
        st.klink = null
        if (apply) setLink(view, input.value.trim(), { from: l.from, to: l.to })
        else view.focus()
        renderBars()
      }
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); done(true) } if (e.key === 'Escape') done(false) })
      kbar.classList.add('on')
      kbar.classList.add('dc-klink')
      put(kbar,
        h('span', { class: 'dc-kl' }, icon('link', 17)),
        input,
        h('button', { class: 'dc-kx', type: 'button', 'aria-label': 'Cancel', onclick: () => done(false) }, icon('x', 16)),
        h('button', { class: 'dc-kok primary', type: 'button', 'aria-label': 'Apply link', onclick: () => done(true) }, icon('check', 16)))
      if (document.activeElement !== input) setTimeout(() => input.focus(), 0)
      return
    }
    kbar.classList.remove('dc-klink')
    if (writing) {
      const a = active(view.state)
      const tools = st.mode === 'edit' ? [
        h('button', { class: 'dc-kb-aa', type: 'button', 'aria-label': 'Text style', onclick: () => openSheet('blocks') }, h('span', { text: 'Aa' }), icon('chevronDown', 12)),
        h('button', { class: a.bold ? 'on' : '', type: 'button', 'aria-label': 'Bold', onclick: () => toggleInline(view, 'bold') }, icon('bold', 19)),
        h('button', { class: a.italic ? 'on' : '', type: 'button', 'aria-label': 'Italic', onclick: () => toggleInline(view, 'italic') }, icon('italic', 19)),
        h('button', { class: a.link ? 'on' : '', type: 'button', 'aria-label': 'Link', onclick: () => { const r = view.state.selection.main; st.klink = { from: r.from, to: r.to, url: linkAt(view.state, r.from) || '' }; renderBars() } }, icon('link', 19)),
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
        canType() && opts.mic?.() ? h('span', { class: 'dc-kmic' }, opts.mic()) : null,
      )
    }
    // A bar that is off keeps nothing (the mic least of all: in Viewing
    // there is no dictating into the doc).
    if (!writing && kbar.firstChild) put(kbar)
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
        h('div', { class: 'dc-ch' }, avatar(n.author, 24), h('span', { class: 'dc-nm', text: name(n.author) }), h('span', { class: 'dc-tm', text: rel(n.created) }), mine(n) && !st.editing ? ownTools(n) : null, h('span', { class: 'dc-grow' }),
          n.kind === 'comment' && mayResolve(n) ? h('button', { class: 'dc-sm ghost dc-resolve', type: 'button', onclick: () => resolve(n).then(closeSheet) }, icon('check', 14), ' Resolve') : null, close),
        ...threadBody(n, { full: true }))]
    } else if (k === 'compose' && st.compose) {
      body = [composeCard()]
    } else if (k === 'blocks') {
      const current = blockOf(view.state)
      body = [h('div', { class: 'dc-sh' }, h('b', { text: 'Text style' }), h('span', { class: 'dc-grow' }), close),
        h('div', { class: 'dc-bsh' }, (blockOf(view.state) === 'p' && !view.state.doc.lineAt(view.state.selection.main.head).text.trim() ? BLOCKS : turnable()).map(b => h('button', { class: `dc-bi${current === b.id ? ' on' : ''}`, type: 'button', onclick: () => { st.sheet = null; setBlock(view, b.id); render() } },
          h('span', { class: `dc-bt${b.glyph ? ' g' : ''}` }, b.icon ? icon(b.icon, 17) : b.glyph), h('span', { class: 'dc-bl', text: b.label }), h('span', { class: 'dc-bk', text: b.md.trim() }))))]
    } else if (k === 'toc') {
      if (!toc.active()) { st.sheet = null; return renderSheet() }
      body = [h('div', { class: 'dc-sh' }, h('b', { text: 'Contents' }), h('span', { class: 'dc-grow' }), close), toc.sheetBody()]
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
    st.lostToast = false
    toast.className = `dc-toast on${bad ? ' bad' : ''}${undo ? '' : ' plain'}`
    put(toast, 
      tick ? h('span', { class: 'dc-tk' }, icon('check', 13)) : null,
      h('span', { text }),
      undo ? h('button', { type: 'button', onclick: () => { toast.classList.remove('on'); undo() } }, 'Undo') : null,
    )
    toastTimer = setTimeout(() => toast.classList.remove('on'), bad ? 7000 : 5000)
  }

  // -- render ---------------------------------------------------------------------------------------------

  let typable = canType()
  function render() {
    if (st.destroyed) return
    if (typable !== canType()) { typable = canType(); opts.onCanType?.(typable) }
    // No notes to show: no margin, the text in the middle. A change of that
    // moves the text, so it waits while keys are going down in it (someone
    // else's note can arrive mid-sentence) and lands at the pause;
    // a note being started is never kept waiting.
    let bare = !st.phone && !st.compose && openNotes().length === 0
    clearTimeout(bareTimer)
    const quiet = Date.now() - typedAt
    if (shownBare != null && bare !== shownBare && !st.compose && view.hasFocus && quiet < TYPING_PAUSE) {
      bare = shownBare
      bareTimer = setTimeout(render, TYPING_PAUSE - quiet + 20)
    }
    shownBare = bare
    root.className = `dc mode-${st.mode}${st.phone ? ' phone' : ' desk'}${st.phone && st.sheet ? ' sheet-open' : ''}${bare ? ' bare' : ''}${st.authors ? ' authors' : ''}${st.reattach ? ' reattaching' : ''}${st.drawer && !st.phone ? ' drawer-open' : ''}`
    renderHead()
    banner.classList.toggle('on', Boolean(st.reattach))
    if (st.reattach) {
      put(banner, icon('reattach', 15), h('span', { text: 'Select the words this note is about, then choose Attach.' }),
        h('button', { class: 'dc-sm ok primary', type: 'button', onclick: finishReattach }, 'Attach'), h('button', { class: 'dc-sm ghost', type: 'button', onclick: () => { st.reattach = null; render() } }, 'Cancel'))
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
  if (st.mode === 'suggest') { view.dispatch({ effects: setSuggestBase.of(st.mapBase), annotations: remote.of(true) }); enterDrafts(); redrawPlaces() }
  if (st.authors) loadAuthors()

  return {
    dom: root,
    view,
    state: st,
    flush,
    setPhone(phone) {
      if (st.phone === Boolean(phone)) return
      // The line at the top stays at the top across the change of width.
      const y = scroll.getBoundingClientRect().top - view.documentTop
      const pos = scroll.scrollTop > 0 && y > 0 ? view.lineBlockAtHeight(y).from : null
      st.phone = Boolean(phone)
      st.sheet = null
      hideSelbar()
      render()
      if (pos != null) {
        view.requestMeasure({
          read: () => (view.documentTop - scroll.getBoundingClientRect().top + view.lineBlockAt(pos).top) / zoom(),
          write: (d) => { scroll.scrollTop += d },
        })
      }
    },
    setSlots({ take: t = null, player: p = null } = {}) {
      if (t !== take.firstChild) put(take, ...(t ? [t] : []))
      if (p !== player.firstChild) put(player, ...(p ? [p] : []))
    },
    refreshHead: () => renderHead(),
    // Whether the doc takes typed text from this person (Editing or
    // Suggesting, with the right to): where dictation may put its words.
    canType: () => canType(),
    // A quiet line above the doc (the share page's "new since your last visit").
    setNotice(node) { put(notice, node); notice.classList.toggle('on', Boolean(node)); queueLayout() },
    focusNote: (id) => focusNote(id, { scrollTo: true }),
    toast: (text, undo = null) => showToast(text, undo),
    notes: () => st.notes,
    me: () => st.me,
    // A fresh read of the doc (focus came back, the file changed on disk).
    reload: () => reloadQuietly(),
    // My drafts while Suggesting (record.js), for tests and tools.
    drafts: () => view.state.field(draftsField).list.map(d => ({ ...d })),
    // The text as the editor holds it; replacing it is an edit like typing.
    // In editor space, with editor positions (what dictation's field reads).
    text: () => view.state.doc.toString(),
    replaceText(next, caret = null) {
      const cur = view.state.doc.toString()
      const cs = ChangeSet.of(diffChanges(cur, next), cur.length, '\n')
      view.dispatch({ changes: cs, selection: caret == null ? undefined : EditorSelection.cursor(Math.min(caret, next.length)), userEvent: 'input.dictation' })
    },
    // Unsaved: an edit not yet saved, or typing in Suggesting not yet sent.
    dirty: () => (st.mode === 'edit' && !st.sinceBase.empty) || st.saving || st.pending,
    destroy() {
      st.destroyed = true
      clearTimeout(saveTimer)
      clearTimeout(suggestTimer)
      clearTimeout(watchTimer)
      globalThis.document?.removeEventListener('keydown', onEscape)
      clearTimeout(gest.timer)
      globalThis.removeEventListener?.('mouseup', onSelectEnd, true)
      globalThis.removeEventListener?.('touchend', onTouchEnd, true)
      globalThis.removeEventListener?.('touchcancel', onTouchEnd, true)
      ro?.disconnect()
      toc.destroy()
      view.destroy()
    },
  }
}
