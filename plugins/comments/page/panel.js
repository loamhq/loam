// Whole-file comments panel for any non-Markdown file (PDF, image, SVG, HTML,
// audio, video, code, archives). Used inside Loam Files (`files.viewer` slot
// `panel`) and on Sharing's standalone `/s/<token>` share page.

import { bottomSheet, grab } from './sheet.js'
import { h, icon, put, tone, TONE_INK, rel as ago } from './editor/dom.js'

const POLL_MS = 2500
const samePerson = (a, b) => Boolean(a && b && a.id && b.id && a.id === b.id)

export function createCommentsPanel({
  api,
  initial = null,
  face = null,
  onGone = null,
  onCount = null,
  readOnly = false,
  title = 'Comments',
}) {
  const st = {
    doc: initial,
    loading: !initial,
    error: '',
    showResolved: false,
    replyOpen: new Set(),
    replyDrafts: new Map(),
    editing: null, // { id, reply, text }
    draft: '',
    busy: false,
    notesRev: initial?.notes_revision || null,
    destroyed: false,
  }

  const dom = h('section', { class: 'dc-panel', 'aria-label': title })
  const headEl = h('div', { class: 'dc-panel-head' })
  const bodyEl = h('div', { class: 'dc-panel-body' })
  const footEl = h('div', { class: 'dc-panel-foot' })
  const toastEl = h('div', { class: 'dc-panel-toast', role: 'status' })
  dom.append(headEl, bodyEl, footEl, toastEl)

  let toastTimer = null
  function toast(text, undo = null, bad = false) {
    toastEl.replaceChildren(document.createTextNode(text))
    toastEl.classList.toggle('bad', bad)
    toastEl.classList.toggle('plain', !undo)
    if (undo) {
      toastEl.append(h('button', {
        type: 'button',
        onclick: () => { toastEl.classList.remove('on'); undo() },
      }, 'Undo'))
    }
    requestAnimationFrame(() => toastEl.classList.add('on'))
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => toastEl.classList.remove('on'), undo ? 6000 : 3200)
  }

  function avatar(author, size = 24) {
    const custom = face?.(author, size)
    if (custom) return h('span', { class: 'dc-av', style: { width: `${size}px`, height: `${size}px` } }, custom)
    const own = author?.kind === 'owner'
    const style = own
      ? { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.46)}px` }
      : { width: `${size}px`, height: `${size}px`, fontSize: `${Math.round(size * 0.46)}px`, background: tone(author?.name || '?'), color: TONE_INK }
    return h('span', { class: 'dc-face', style, text: (author?.name || '?').charAt(0).toUpperCase() })
  }

  const canComment = () => !readOnly && Boolean(st.doc?.can?.comment)
  const canResolve = (n) => {
    if (readOnly || !st.doc?.me) return false
    if (st.doc?.can?.resolve_any) return true
    return samePerson(n.author, st.doc.me)
  }
  const isMine = (author) => !readOnly && Boolean(st.doc?.me) && samePerson(author, st.doc.me)
  const openNotes = () => (st.doc?.notes || []).filter(n => n.state === 'open')
  const closedNotes = () => (st.doc?.notes || []).filter(n => n.state !== 'open')

  function notifyCount() {
    onCount?.(openNotes().length, closedNotes().length)
  }

  function applyDoc(doc) {
    st.doc = doc
    st.loading = false
    st.error = ''
    st.notesRev = doc?.notes_revision || null
    notifyCount()
    draw()
  }

  async function load() {
    try {
      const doc = await api.get()
      if (st.destroyed) return
      applyDoc(doc)
    } catch (err) {
      if (st.destroyed) return
      if (err?.status === 404 || err?.status === 410) {
        onGone?.()
        return
      }
      st.loading = false
      st.error = err?.message || 'Comments could not be loaded.'
      draw()
    }
  }

  async function poll() {
    if (st.destroyed || st.loading || st.busy || document.hidden) return
    try {
      const r = await api.revision()
      if (st.destroyed) return
      if (r && r.notes !== st.notesRev) {
        const doc = await api.get()
        if (!st.destroyed) applyDoc(doc)
      }
    } catch (err) {
      if (err?.status === 404 || err?.status === 410) onGone?.()
    }
  }

  const pollId = setInterval(poll, POLL_MS)

  function draw() {
    const open = openNotes()
    const closed = closedNotes()

    // Header
    put(headEl,
      h('div', { class: 'dc-panel-title' },
        h('h2', { text: title }),
        open.length ? h('span', { class: 'dc-panel-count', text: String(open.length) }) : null),
      h('div', { class: 'dc-panel-head-end' },
        closed.length ? h('button', {
          type: 'button',
          class: `dc-panel-tog${st.showResolved ? ' on' : ''}`,
          onclick: () => { st.showResolved = !st.showResolved; draw() },
          text: st.showResolved ? 'Open' : `Resolved (${closed.length})`,
        }) : null,
        sheetScrim ? h('button', {
          type: 'button', class: 'dc-ib', 'aria-label': 'Close comments', onclick: closeSheet,
        }, icon('x', 18)) : null))

    // Body
    if (st.loading) {
      put(bodyEl, h('div', { class: 'dc-panel-empty', text: 'Loading comments…' }))
    } else if (st.error) {
      put(bodyEl,
        h('div', { class: 'dc-panel-empty' },
          h('p', { text: st.error }),
          h('button', { type: 'button', class: 'dc-sm', onclick: () => { st.loading = true; st.error = ''; draw(); load() } }, 'Try again')))
    } else if (st.showResolved) {
      if (!closed.length) {
        put(bodyEl, h('div', { class: 'dc-panel-empty', text: 'No resolved comments.' }))
      } else {
        put(bodyEl, h('div', { class: 'dc-panel-list' }, closed.map(n => threadCard(n, true))))
      }
    } else if (!open.length) {
      put(bodyEl, h('div', { class: 'dc-panel-empty' },
        h('p', { text: canComment() ? 'No comments yet. Leave a note on this file below.' : 'No comments yet.' })))
    } else {
      put(bodyEl, h('div', { class: 'dc-panel-list' }, open.map(n => threadCard(n, false))))
    }

    // Footer composer (only when user can comment; zero dead controls)
    if (canComment() && !st.showResolved) {
      const existing = footEl.querySelector('textarea.dc-panel-in')
      const hadFocus = existing && document.activeElement === existing
      const ta = h('textarea', {
        class: 'dc-panel-in',
        rows: '2',
        placeholder: 'Add a comment…',
        'aria-label': 'Add a comment',
        disabled: st.busy ? true : null,
      })
      ta.value = st.draft
      const sendBtn = h('button', {
        type: 'button',
        class: 'dc-sm primary dc-panel-send',
        disabled: st.busy || !st.draft.trim() ? true : null,
        onclick: () => submitComment(),
      }, st.busy ? 'Posting…' : 'Comment')
      ta.addEventListener('input', () => {
        st.draft = ta.value
        sendBtn.disabled = st.busy || !st.draft.trim()
      })
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault()
          if (st.draft.trim() && !st.busy) submitComment()
        }
      })
      put(footEl,
        h('div', { class: 'dc-panel-comp' },
          ta,
          h('div', { class: 'dc-panel-comp-bar' },
            h('span', { class: 'dc-panel-hint', text: 'Whole-file comment' }),
            sendBtn)))
      footEl.hidden = false
      if (hadFocus) ta.focus()
    } else {
      footEl.replaceChildren()
      footEl.hidden = true
    }
  }

  function threadCard(n, resolved) {
    const me = st.doc?.me
    const viaLink = me && (me.kind === 'owner' || me.kind === 'agent') && String(n.author?.kind || '').includes('guest')
    const editingRoot = st.editing && st.editing.id === n.id && !st.editing.reply

    const headTools = []
    if (!resolved && isMine(n.author) && !editingRoot) {
      headTools.push(h('span', { class: 'dc-own' },
        h('button', {
          type: 'button', class: 'dc-ib', title: 'Edit comment', 'aria-label': 'Edit comment',
          onclick: () => { st.editing = { id: n.id, reply: null, text: n.text || '' }; draw() },
        }, icon('pencil', 13)),
        !(n.replies || []).length ? h('button', {
          type: 'button', class: 'dc-ib', title: 'Delete comment', 'aria-label': 'Delete comment',
          onclick: () => removeComment(n.id),
        }, icon('trash', 13)) : null))
    }
    if (!resolved && canResolve(n)) {
      headTools.push(h('button', {
        type: 'button', class: 'dc-ib dc-panel-res', title: 'Resolve', 'aria-label': 'Resolve comment',
        onclick: () => resolveComment(n.id),
      }, icon('check', 15)))
    }
    if (resolved && canResolve(n)) {
      headTools.push(h('button', {
        type: 'button', class: 'dc-sm ghost',
        onclick: () => reopenComment(n.id),
      }, 'Reopen'))
    }

    const bodyNode = editingRoot
      ? editBox(st.editing)
      : h('p', { class: 'dc-tx', text: n.text || '' })

    const replies = (n.replies || []).map(r => replyRow(n, r, resolved))

    let replyBox = null
    if (!resolved && canComment()) {
      const opened = st.replyOpen.has(n.id) || (n.replies || []).length > 0
      if (opened) {
        const draft = st.replyDrafts.get(n.id) || ''
        const inp = h('input', {
          class: 'dc-input', type: 'text', placeholder: 'Reply…', 'aria-label': 'Reply',
          value: draft,
        })
        const cmp = h('div', { class: `dc-cmp${draft.trim() ? ' has' : ''}` })
        const send = h('button', {
          type: 'button', class: 'dc-send primary', 'aria-label': 'Send reply',
          onclick: () => submitReply(n.id),
        }, icon('send', 14))
        inp.addEventListener('input', () => {
          st.replyDrafts.set(n.id, inp.value)
          cmp.classList.toggle('has', Boolean(inp.value.trim()))
        })
        inp.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.isComposing && inp.value.trim()) {
            e.preventDefault()
            submitReply(n.id)
          }
        })
        cmp.append(inp, send)
        replyBox = cmp
      } else {
        replyBox = h('button', {
          type: 'button', class: 'dc-panel-replybtn',
          onclick: () => { st.replyOpen.add(n.id); draw(); dom.querySelector(`[data-tid="${n.id}"] .dc-cmp input`)?.focus() },
        }, 'Reply')
      }
    }

    return h('article', { class: `dc-pcard${resolved ? ' resolved' : ''}`, 'data-tid': n.id },
      h('div', { class: 'dc-ch' },
        avatar(n.author, 24),
        h('span', { class: 'dc-nm', text: n.author?.name || 'Unknown' }),
        viaLink ? h('span', { class: 'dc-via', text: 'via link' }) : null,
        h('span', { class: 'dc-tm', text: ago(n.created) }),
        h('span', { class: 'dc-grow' }),
        ...headTools),
      bodyNode,
      replies.length ? h('div', { class: 'dc-rps' }, ...replies) : null,
      replyBox)
  }

  function replyRow(n, r, resolved) {
    const me = st.doc?.me
    const viaLink = me && (me.kind === 'owner' || me.kind === 'agent') && String(r.author?.kind || '').includes('guest')
    const editingHere = st.editing && st.editing.id === n.id && st.editing.reply === r.id
    return h('div', { class: 'dc-rp' },
      avatar(r.author, 18),
      h('div', {},
        h('div', { class: 'dc-rh' },
          h('b', { text: r.author?.name || 'Unknown' }),
          viaLink ? h('span', { class: 'dc-via', text: 'via link' }) : null,
          h('span', { class: 'dc-tm', text: ago(r.created) }),
          !resolved && isMine(r.author) && !editingHere ? h('span', { class: 'dc-own' },
            h('button', {
              type: 'button', class: 'dc-ib', title: 'Edit reply', 'aria-label': 'Edit reply',
              onclick: () => { st.editing = { id: n.id, reply: r.id, text: r.text || '' }; draw() },
            }, icon('pencil', 12)),
            h('button', {
              type: 'button', class: 'dc-ib', title: 'Delete reply', 'aria-label': 'Delete reply',
              onclick: () => deleteReply(n.id, r.id),
            }, icon('trash', 12))) : null),
        editingHere ? editBox(st.editing) : h('div', { class: 'dc-rt', text: r.text || '' })))
  }

  function editBox(ed) {
    const ta = h('textarea', { rows: '2', 'aria-label': 'Edit text' })
    ta.value = ed.text
    ta.addEventListener('input', () => { ed.text = ta.value })
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { st.editing = null; draw() }
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveEdit() }
    })
    setTimeout(() => ta.focus(), 10)
    return h('div', { class: 'dc-edit-fld' },
      h('div', { class: 'dc-fld' }, ta),
      h('div', { class: 'dc-dbtns' },
        h('button', { type: 'button', class: 'dc-sm ghost', onclick: () => { st.editing = null; draw() } }, 'Cancel'),
        h('button', { type: 'button', class: 'dc-sm primary', onclick: saveEdit }, 'Save')))
  }

  async function submitComment() {
    const text = st.draft.trim()
    if (!text || st.busy) return
    st.busy = true
    draw()
    try {
      const doc = await api.add({ kind: 'comment', text })
      st.draft = ''
      st.busy = false
      applyDoc(doc)
    } catch (err) {
      st.busy = false
      draw()
      toast(err?.message || 'Could not post comment.', null, true)
    }
  }

  async function submitReply(tid) {
    const text = (st.replyDrafts.get(tid) || '').trim()
    if (!text) return
    try {
      const doc = await api.reply(tid, text)
      st.replyDrafts.delete(tid)
      applyDoc(doc)
    } catch (err) {
      toast(err?.message || 'Could not post reply.', null, true)
    }
  }

  async function saveEdit() {
    const ed = st.editing
    if (!ed || !ed.text.trim()) return
    try {
      const doc = await api.edit(ed.id, ed.reply, ed.text.trim())
      st.editing = null
      applyDoc(doc)
    } catch (err) {
      toast(err?.message || 'Could not save edit.', null, true)
    }
  }

  async function removeComment(tid) {
    try {
      const doc = await api.removeNote(tid)
      applyDoc(doc)
      toast('Comment deleted')
    } catch (err) {
      toast(err?.message || 'Could not delete comment.', null, true)
    }
  }

  async function deleteReply(tid, rid) {
    try {
      const doc = await api.deleteReply(tid, rid)
      applyDoc(doc)
      toast('Reply deleted')
    } catch (err) {
      toast(err?.message || 'Could not delete reply.', null, true)
    }
  }

  async function resolveComment(tid) {
    try {
      const doc = await api.resolve(tid)
      applyDoc(doc)
      toast('Comment resolved', () => reopenComment(tid))
    } catch (err) {
      toast(err?.message || 'Could not resolve comment.', null, true)
    }
  }

  async function reopenComment(tid) {
    try {
      const doc = await api.reopen(tid)
      st.showResolved = false
      applyDoc(doc)
      toast('Comment reopened')
    } catch (err) {
      toast(err?.message || 'Could not reopen comment.', null, true)
    }
  }

  // Phone sheet wrapper: temporarily moves `dom` into a bottom sheet, then restores on close.
  let sheetScrim = null
  let sheetBs = null
  let sheetHome = null
  function openSheet() {
    if (sheetScrim) return
    sheetHome = dom.parentNode
    sheetScrim = h('div', { class: 'dc-dlg-scrim dc-panel-scrim', onclick: (e) => { if (e.target === sheetScrim) closeSheet() } })
    sheetBs = bottomSheet(sheetScrim, { close: closeSheet })
    const dlg = h('div', { class: 'dc-dlg dc-panel-dlg', role: 'dialog', 'aria-label': title },
      grab(h),
      dom)
    sheetScrim.append(dlg)
    document.body.append(sheetScrim)
    draw()
    requestAnimationFrame(() => sheetScrim?.classList.add('on'))
    document.addEventListener('keydown', onSheetKey)
  }

  function onSheetKey(e) { if (e.key === 'Escape') closeSheet() }

  function closeSheet() {
    if (!sheetScrim) return
    const s = sheetScrim
    sheetScrim = null
    sheetBs?.dispose()
    sheetBs = null
    document.removeEventListener('keydown', onSheetKey)
    s.classList.remove('on')
    setTimeout(() => {
      if (sheetHome && sheetHome.isConnected) sheetHome.append(dom)
      s.remove()
      if (!st.destroyed) draw()
    }, 240)
  }

  function destroy() {
    st.destroyed = true
    clearInterval(pollId)
    clearTimeout(toastTimer)
    closeSheet()
  }

  if (initial) {
    notifyCount()
    draw()
  } else {
    draw()
    load()
  }

  return {
    dom,
    reload: load,
    openSheet,
    closeSheet,
    destroy,
    openCount: () => openNotes().length,
    notes: () => st.doc?.notes || [],
  }
}
