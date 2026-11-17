// The doc's table of contents: never above the text, always within reach.
//
// - Desktop: a short dash per heading on the text's left (the notes keep the
//   right margin), longer for sections and shorter for subsections, the one
//   being read darker, centred in the visible pane. Hovering or focusing them
//   opens the titles in a small floating card; a title jumps there.
// - Phone and the side panel (the phone layout): the bar's title becomes the
//   section being read, changing as the doc scrolls; a tap opens the list in
//   the editor's bottom sheet.
//
// Which headings: the two levels below the doc's title. A single `#` heading
// that comes first is the title and is left out; of what remains, the top two
// levels present count (usually `##` and `###`). Only headings of the doc's
// own flow count: never one inside code, a quote or a list. Fewer than three:
// no contents at all, so a short doc stays clean.
//
// It is the editor's (editor.js creates it): the headings come from the
// editor's own syntax tree, so it follows every change, typed or arriving.

import { ensureSyntaxTree, syntaxTree } from '../vendor/codemirror.js'
import { h, put, icon } from './dom.js'
import { frontmatterRange } from './live.js'

export const MIN_HEADINGS = 3

// A heading's words as read: Markdown's marks taken off.
export function plainHeading(s) {
  return String(s)
    .replace(/!\[\[[^\]]*\]\]/g, '')
    .replace(/\[\[([^\]|]*)\|([^\]]*)\]\]/g, '$2')
    .replace(/\[\[([^\]]*)\]\]/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/(\*\*|__)(?=\S)(.+?)\1/g, '$2')
    .replace(/(^|[^\w*])\*(?=\S)([^*]+?)\*(?!\w)/g, '$1$2')
    .replace(/(^|[^\w])_(?=\S)([^_]+?)_(?!\w)/g, '$1$2')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/==(.+?)==/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/\\([\\`*_{}[\]()#+\-.!|~<>])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

// Every heading of the doc's own flow: { from, level, text }.
function allHeadings(state, tree) {
  const out = []
  const fm = frontmatterRange(state.doc)
  for (let n = tree.topNode.firstChild; n; n = n.nextSibling) {
    if (fm && n.from < fm.to) continue
    let m = /^ATXHeading(\d)$/.exec(n.name)
    if (m) {
      const text = plainHeading(state.sliceDoc(n.from, n.to).replace(/^\s*#{1,6}(?=\s|$)/, '').replace(/\s#+\s*$/, ''))
      if (text) out.push({ from: n.from, level: Number(m[1]), text })
      continue
    }
    m = /^SetextHeading(\d)$/.exec(n.name)
    if (m) {
      const src = state.sliceDoc(n.from, n.to).split(/\r?\n/)
      const text = plainHeading(src.slice(0, -1).join(' '))
      if (text) out.push({ from: n.from, level: Number(m[1]), text })
    }
  }
  return out
}

// The contents: [{ from, level, text, depth }] (depth 0 a section, 1 a
// subsection), or [] when the doc has fewer than three. `complete` says
// whether the whole doc was read (a very long one is parsed in steps).
export function headingsOf(state, timeout = 40) {
  const full = ensureSyntaxTree(state, state.doc.length, timeout)
  const all = allHeadings(state, full || syntaxTree(state))
  let list = all
  if (all.length && all[0].level === 1 && all.filter(x => x.level === 1).length === 1) list = all.slice(1)
  const levels = [...new Set(list.map(x => x.level))].sort((a, b) => a - b).slice(0, 2)
  const items = list.filter(x => levels.includes(x.level)).map(x => ({ ...x, depth: x.level === levels[0] ? 0 : 1 }))
  return { items: items.length >= MIN_HEADINGS ? items : [], complete: Boolean(full) }
}

function setStyle(el, k, v) { if (el.style[k] !== v) el.style[k] = v }

const reduced = () => Boolean(globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

// opts: view, root, scroll, col (the editor's DOM), zoom() and local(rect, el)
// (its placing helpers), phone() (which layout), title() (the doc's name as
// the bar shows it), openList() (the phone's sheet), onActive() (the contents
// appeared or went: the bar is drawn again).
export function createToc(opts) {
  const { view, root, scroll, col, zoom, local } = opts
  let items = [], key = '', cur = -1, hold = -1, queued = false, retry = null, destroyed = false
  let open = false, closeTimer = null, openTimer = null, quietFocus = false
  let jumpRun = 0

  // -- desktop: the ticks and their card --
  const ticks = h('button', { class: 'dc-ticks', type: 'button', 'aria-label': 'Contents', 'aria-expanded': 'false', title: 'Contents' })
  const cardList = h('div', { class: 'dc-toclist' })
  const card = h('div', { class: 'dc-toccard' }, cardList)
  const rail = h('nav', { class: 'dc-toc', 'aria-label': 'Contents' }, ticks, card)
  // -- phone: the bar's title, and the sheet's list --
  const docName = h('span', { class: 'dc-secdoc' })
  const secName = h('span', { class: 'dc-secname' })
  const secIcon = h('span', { class: 'dc-secic' }, icon('list', 15))
  const titleBtn = h('button', { class: 'dc-secbtn', type: 'button', 'aria-haspopup': 'dialog', onclick: () => opts.openList?.() },
    secIcon, h('span', { class: 'dc-secstack' }, docName, secName), h('span', { class: 'dc-seccv' }, icon('chevronDown', 12)))
  const sheetList = h('div', { class: 'dc-toclist dc-toclist-sheet' })
  root.append(rail)

  // -- reading the headings --
  function refresh() {
    if (destroyed) return
    clearTimeout(retry)
    const { items: next, complete } = headingsOf(view.state)
    if (!complete) retry = setTimeout(queue, 250)
    const k = JSON.stringify(next.map(x => [x.depth, x.text]))
    const was = items.length > 0
    items = next
    if (k !== key) {
      key = k
      if (hold >= items.length) hold = -1
      drawLists()
      drawTicks()
    }
    if (was !== (items.length > 0)) opts.onActive?.()
    place()
    spy()
  }
  function queue() {
    if (queued || destroyed) return
    queued = true
    requestAnimationFrame(() => { queued = false; refresh() })
  }

  function item(x, i, where) {
    return h('button', { class: `dc-tocitem d${x.depth}`, type: 'button', dataset: { i: String(i) }, title: x.text, onclick: (e) => pick(i, e, where) },
      h('span', { text: x.text }))
  }
  function drawLists() {
    put(cardList, items.map((x, i) => item(x, i, 'card')))
    put(sheetList,
      h('button', { class: 'dc-tocitem dc-toctop', type: 'button', dataset: { i: '-1' }, onclick: (e) => pick(-1, e, 'sheet') },
        h('span', { text: opts.title?.() || 'Top' })),
      items.map((x, i) => item(x, i, 'sheet')))
    mark()
  }
  // Many headings: the dashes close up (never under 4 px apart); past what
  // even that holds, only the sections keep a dash.
  let pitch = 11, onlySections = false
  function drawTicks() {
    const paneH = scroll.clientHeight || 600
    const room = Math.max(120, paneH * 0.62)
    onlySections = items.length * 4 > room
    const shown = onlySections ? items.filter(x => x.depth === 0).length : items.length
    pitch = Math.max(4, Math.min(11, Math.floor(room / Math.max(1, shown))))
    ticks.style.setProperty('--dc-tkgap', `${pitch - 2}px`)
    put(ticks, items.map((x, i) => (onlySections && x.depth ? null : h('i', { class: `d${x.depth}`, dataset: { i: String(i) }, 'aria-hidden': 'true' }))))
    mark()
  }

  // -- where the reader is --
  function headingTop(from) {
    // in the page's pixels, from the top of the scrolled content
    const z = zoom()
    return (view.documentTop - scroll.getBoundingClientRect().top + view.lineBlockAt(from).top) / z + scroll.scrollTop
  }
  function current() {
    if (!items.length) return -1
    if (hold >= 0 && hold < items.length) return hold
    const y = scroll.scrollTop + 72
    let c = -1
    for (let i = 0; i < items.length; i++) {
      if (headingTop(items[i].from) <= y) c = i
      else break
    }
    return c
  }
  function spy() {
    if (destroyed) return
    const c = current()
    if (c === cur) return
    cur = c
    mark()
    setTitle()
  }
  function mark() {
    const tickOn = cur >= 0 && onlySections && items[cur]?.depth ? sectionOf(cur) : cur
    for (const el of ticks.children) {
      const on = Number(el.dataset.i) === tickOn
      if (el.classList.contains('on') !== on) el.classList.toggle('on', on)
    }
    for (const list of [cardList, sheetList]) {
      for (const el of list.children) {
        const on = Number(el.dataset.i) === cur
        if (el.classList.contains('on') !== on) el.classList.toggle('on', on)
        if (on) el.setAttribute('aria-current', 'location')
        else if (el.hasAttribute('aria-current')) el.removeAttribute('aria-current')
      }
    }
  }
  function sectionOf(i) {
    for (let j = i; j >= 0; j--) if (items[j].depth === 0) return j
    return -1
  }

  // The phone's bar: the doc's name above the first heading, then the
  // section being read, the change a small fade.
  let shownSec = null, swapTimer = null
  function setTitle() {
    const label = cur >= 0 ? items[cur].text : null
    const name = opts.title?.() || ''
    if (docName.textContent !== name) docName.textContent = name
    const aria = `Contents: ${label || name}`
    if (titleBtn.getAttribute('aria-label') !== aria) titleBtn.setAttribute('aria-label', aria)
    if (label === shownSec) return
    const from = shownSec
    shownSec = label
    clearTimeout(swapTimer)
    titleBtn.classList.toggle('insec', Boolean(label))
    if (!label) return
    if (from && !reduced()) {
      secName.classList.add('swap')
      swapTimer = setTimeout(() => { secName.textContent = label; secName.classList.remove('swap') }, 140)
    } else {
      secName.textContent = label
      secName.classList.remove('swap')
    }
  }

  // -- placing the ticks --
  function place() {
    const on = items.length > 0 && !opts.phone()
    if (rail.classList.contains('on') !== on) rail.classList.toggle('on', on)
    if (!on) { if (open) setOpen(false); return }
    const paneH = scroll.clientHeight
    if (!paneH) return
    const sr = local(scroll.getBoundingClientRect())
    const textLeft = local(view.contentDOM.getBoundingClientRect()).left
    // Well clear of what sits at the text's left (Viewing's note marks, the
    // "+"), and never closer than 14 px to the pane's edge.
    const left = Math.max(sr.left + 14, textLeft - 92)
    // (Only what changed: an idle page must change nothing.)
    setStyle(rail, 'left', `${Math.round(left)}px`)
    setStyle(rail, 'top', `${Math.round(sr.top)}px`)
    setStyle(rail, 'height', `${Math.round(paneH)}px`)
    const want = Math.max(4, Math.min(11, Math.floor(Math.max(120, paneH * 0.62) / Math.max(1, onlySections ? items.filter(x => !x.depth).length : items.length))))
    if (want !== pitch) drawTicks()
    setStyle(card, 'maxHeight', `${Math.max(160, paneH - 24)}px`)
  }

  function setOpen(yes) {
    clearTimeout(closeTimer); clearTimeout(openTimer)
    if (open === yes) return
    open = yes
    rail.classList.toggle('open', yes)
    ticks.setAttribute('aria-expanded', String(yes))
    if (yes) {
      // The one being read in view inside a long card.
      const el = cardList.querySelector('.on') || cardList.firstChild
      if (el && card.scrollHeight > card.clientHeight) {
        card.scrollTop = Math.max(0, el.offsetTop - (card.clientHeight - el.offsetHeight) / 2)
      }
    }
  }
  rail.addEventListener('mouseenter', () => { clearTimeout(closeTimer); openTimer = setTimeout(() => setOpen(true), 60) })
  rail.addEventListener('mouseleave', () => { clearTimeout(openTimer); closeTimer = setTimeout(() => setOpen(false), 180) })
  rail.addEventListener('focusin', () => { if (!quietFocus) setOpen(true) })
  rail.addEventListener('focusout', (e) => { if (!rail.contains(e.relatedTarget)) { quietFocus = false; setOpen(false) } })
  ticks.addEventListener('click', () => setOpen(!open))
  rail.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && open) {
      e.preventDefault(); e.stopPropagation()
      quietFocus = true
      setOpen(false)
      ticks.focus({ preventScroll: true })
      return
    }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && open) {
      const list = [...cardList.children]
      if (!list.length) return
      e.preventDefault()
      const at = list.indexOf(document.activeElement)
      const next = at < 0 ? (cardList.querySelector('.on') ? list.indexOf(cardList.querySelector('.on')) : 0) : Math.max(0, Math.min(list.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)))
      list[next].focus()
    }
  })

  // -- going there --
  function pick(i, e, where) {
    const byKey = e && e.detail === 0
    if (where === 'card') { quietFocus = true; setOpen(false) }
    opts.picked?.(where)
    jump(i, { focus: byKey })
  }
  function target(i) {
    if (i < 0) return 0
    const from = items[i].from
    const c = view.coordsAtPos(from, 1)
    const z = zoom()
    const sr = scroll.getBoundingClientRect()
    // Measured where drawn (the words' own top), else from the height map
    // (the line's top, above its own padding).
    const top = c ? (c.top - sr.top) / z + scroll.scrollTop : headingTop(from) + 18
    return Math.max(0, Math.min(scroll.scrollHeight - scroll.clientHeight, top - 30))
  }
  function jump(i, { focus = false } = {}) {
    const run = ++jumpRun
    hold = i
    spy()
    if (i < 0) hold = -1
    const smooth = !reduced()
    scroll.scrollTo({ top: target(i), behavior: smooth ? 'smooth' : 'auto' })
    // Until CodeMirror has drawn the lines on the way, their heights are
    // estimates: once the scroll settles, measure again and correct.
    let tries = 0
    const settle = () => {
      if (destroyed || run !== jumpRun) return
      const t = target(i)
      if (Math.abs(t - scroll.scrollTop) > 2 && tries++ < 4) {
        scroll.scrollTo({ top: t, behavior: 'auto' })
        return setTimeout(settle, 80)
      }
      if (i >= 0) flash(items[i]?.from)
      if (focus) {
        const pos = i >= 0 ? items[i].from : 0
        view.focus()
        view.dispatch({ selection: { anchor: Math.min(pos, view.state.doc.length) } })
        // focusing may have scrolled the caret into view; keep the place
        scroll.scrollTo({ top: target(i), behavior: 'auto' })
      }
    }
    if (smooth) waitScroll(settle)
    else requestAnimationFrame(() => setTimeout(settle, 30))
  }
  // A smooth scroll is over when it stops moving (scrollend where there is
  // one, and a still frame count as a fallback).
  function waitScroll(done) {
    let last = scroll.scrollTop, still = 0, finished = false
    const end = () => { if (finished) return; finished = true; scroll.removeEventListener('scrollend', end); done() }
    scroll.addEventListener('scrollend', end, { once: true })
    const tick = () => {
      if (finished || destroyed) return
      if (Math.abs(scroll.scrollTop - last) < 0.5) still++
      else { still = 0; last = scroll.scrollTop }
      if (still > 6) return end()
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
    setTimeout(end, 1500)
  }
  // A brief soft light on the heading arrived at.
  function flash(from) {
    if (from == null) return
    const a = view.coordsAtPos(from, 1)
    const line = view.state.doc.lineAt(from)
    const b = view.coordsAtPos(line.to, -1)
    if (!a || !b) return
    const content = view.contentDOM.getBoundingClientRect()
    const r = local({ left: content.left, right: content.right, top: a.top, bottom: Math.max(a.bottom, b.bottom) }, col)
    col.querySelector('.dc-tocflash')?.remove()
    const el = h('div', { class: 'dc-tocflash', 'aria-hidden': 'true', style: { left: `${r.left - 10}px`, top: `${r.top - 3}px`, width: `${r.right - r.left + 20}px`, height: `${r.bottom - r.top + 6}px` } })
    // beneath the words: before CodeMirror's own (positioned) box
    col.insertBefore(el, view.dom.parentNode === col ? view.dom : null)
    el.addEventListener('animationend', () => el.remove())
    setTimeout(() => el.remove(), 2000)
  }

  // A scroll the reader makes lets go of the heading just jumped to.
  const letGo = () => { if (hold >= 0) { hold = -1; spy() } }
  for (const ev of ['wheel', 'touchstart', 'mousedown']) scroll.addEventListener(ev, letGo, { passive: true })
  scroll.addEventListener('keydown', letGo)
  let spyQueued = false
  scroll.addEventListener('scroll', () => {
    if (spyQueued) return
    spyQueued = true
    requestAnimationFrame(() => { spyQueued = false; spy() })
  }, { passive: true })

  queue()

  return {
    // The doc changed, or anything that moves lines did.
    changed(doc) { if (doc) queue(); else { place(); spy() } },
    active: () => items.length > 0,
    // The bar's title on a phone: the doc's name, then the section.
    titleNode() {
      if (!items.length) return null
      docName.textContent = opts.title?.() || ''
      setTitle()
      return titleBtn
    },
    // The phone sheet's list, the one being read in view.
    sheetBody() {
      mark()
      // The list scrolls under the sheet's own head, which stays.
      requestAnimationFrame(() => {
        const el = sheetList.querySelector('.on')
        if (el && sheetList.scrollHeight > sheetList.clientHeight) sheetList.scrollTop = Math.max(0, el.offsetTop - (sheetList.clientHeight - el.offsetHeight) / 2)
        else sheetList.scrollTop = 0
      })
      return sheetList
    },
    jump,
    items: () => items.map(x => ({ ...x })),
    current: () => cur,
    destroy() { destroyed = true; clearTimeout(retry); clearTimeout(closeTimer); clearTimeout(openTimer); clearTimeout(swapTimer); rail.remove() },
  }
}
