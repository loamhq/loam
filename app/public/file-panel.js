// Host controller for companion file panels (`files.viewer`, `slot: 'panel'`,
// such as whole-file Comments on non-Markdown files).
//
// Core owns the toolbar toggle with its unresolved count, the desktop column /
// narrow drawer / phone sheet chrome, close (×), Escape-inside-panel, focus
// handoff (focus composer only when empty), per-device persistence across files
// and reloads, and the default rule: closed when empty, open automatically when
// unresolved notes exist until the user closes it.
import { h, icon } from './ui.js'
import { emptyState } from './empty-state.js'

const observers = new WeakMap()
const wiredInputs = new WeakSet()

export function createPanelState() {
  return {
    key: null,
    wide: null,
    open: false,
    autoOpened: false,
    count: 0,
    resolved: 0,
    loading: true,
    showingResolved: false,
    title: 'Comments',
    pendingFocus: false,
    returnFocus: false,
    lastNode: null,
  }
}

function hasTag(node, tag) {
  const want = tag.toUpperCase()
  for (const child of node?.childNodes ?? []) {
    if (String(child.nodeName ?? child.tagName ?? '').toUpperCase() === want) return true
    if (hasTag(child, want)) return true
  }
  return false
}

function findTag(node, tag) {
  const want = tag.toUpperCase()
  for (const child of node?.childNodes ?? []) {
    if (String(child.nodeName ?? child.tagName ?? '').toUpperCase() === want) return child
    const hit = findTag(child, want)
    if (hit) return hit
  }
  return null
}

function readCountFromDom(node) {
  const raw = node?.querySelector?.('.dc-panel-count')?.textContent?.trim() ?? ''
  if (!raw) return 0
  const n = Number.parseInt(raw, 10)
  return Number.isFinite(n) && n > 0 ? n : 0
}

function readResolvedFromDom(node) {
  const tog = node?.querySelector?.('.dc-panel-tog')
  if (!tog) return 0
  if (tog.classList?.contains?.('on')) {
    return node.querySelectorAll?.('.dc-pcard.resolved')?.length ?? 0
  }
  const m = /\((\d+)\)/.exec(tog.textContent ?? '')
  return m ? Number.parseInt(m[1], 10) : 0
}

function readLoadingFromDom(node) {
  if (!node?.querySelector) return false
  if (node.querySelector('.dc-panel-list') || node.querySelector('.dc-panel-comp')) return false
  const empty = node.querySelector('.dc-panel-empty')
  if (!empty) return false
  if (empty.dataset?.loamEmpty || empty.querySelector('.empty-state') || hasTag(empty, 'p') || hasTag(empty, 'button')) return false
  return /^loading/i.test((empty.textContent ?? '').trim())
}

export function inspectPanel(item) {
  if (!item) return null
  const isNode = Boolean(item.nodeType || item.__keep)
  const rawNode = isNode ? item : (item.node ?? null)
  const realNode = rawNode?.__keep ?? rawNode
  const action = isNode ? null : (item.action ?? null)
  if (!rawNode && !action) return null
  const titleWrap = realNode?.querySelector?.('.dc-panel-title')
  const title = (typeof item.title === 'string' && item.title.trim())
    || realNode?.getAttribute?.('aria-label')?.trim()
    || findTag(titleWrap, 'h2')?.textContent?.trim()
    || 'Comments'
  const count = Number.isInteger(item.count) ? Math.max(0, item.count) : readCountFromDom(realNode)
  const resolved = Number.isInteger(item.resolvedCount) ? Math.max(0, item.resolvedCount) : readResolvedFromDom(realNode)
  const loading = typeof item.loading === 'boolean' ? item.loading : readLoadingFromDom(realNode)
  const tog = realNode?.querySelector?.('.dc-panel-tog')
  const showingResolved = Boolean(tog?.classList?.contains?.('on'))
  return { rawNode, realNode, action, title, count, resolved, loading, showingResolved }
}

// Pure state transition for a file's hosted panel.
// `pref` is the device preference: 'open' | 'closed' | null (automatic).
export function syncPanelState(panel, { key, wide, info, pref }) {
  const fileChanged = panel.key !== key
  const modeChanged = panel.wide !== wide
  panel.title = info.title
  panel.count = info.count
  panel.resolved = info.resolved
  panel.loading = info.loading
  panel.showingResolved = info.showingResolved

  if (fileChanged || modeChanged) {
    panel.key = key
    panel.wide = wide
    panel.autoOpened = false
    panel.pendingFocus = false
    panel.returnFocus = false
    if (!wide) {
      // On phones, never cover a newly opened file with a modal sheet on load.
      panel.open = false
    } else if (pref === 'open') {
      panel.open = true
    } else if (pref === 'closed') {
      panel.open = false
    } else if (!info.loading && info.count > 0) {
      panel.open = true
      panel.autoOpened = true
    } else {
      panel.open = false
    }
    return true
  }

  // Automatic desktop open once comments finish loading with unresolved notes.
  if (wide && pref === null && !panel.open && !panel.autoOpened && !info.loading && info.count > 0) {
    panel.open = true
    panel.autoOpened = true
    return true
  }
  return false
}

function syncTextareaHeight(ta) {
  if (!ta?.style) return
  if (!ta.value) {
    ta.style.height = ''
    return
  }
  ta.style.height = 'auto'
  const hPx = Math.min(Math.max(ta.scrollHeight || 36, 36), 132)
  ta.style.height = `${hPx}px`
}

function polishComposer(node) {
  const ta = node?.querySelector?.('.dc-panel-in')
  if (!ta) return
  if (ta.getAttribute?.('rows') !== '1') ta.setAttribute?.('rows', '1')
  const comp = ta.closest?.('.dc-panel-comp') ?? node?.querySelector?.('.dc-panel-comp')
  const sync = () => {
    comp?.classList?.toggle?.('has-text', Boolean((ta.value ?? '').trim()))
    syncTextareaHeight(ta)
  }
  if (!wiredInputs.has(ta)) {
    wiredInputs.add(ta)
    ta.addEventListener?.('input', sync)
  }
  sync()
}

function polishEmptyState(node) {
  const empty = node?.querySelector?.('.dc-panel-empty')
  if (!empty || empty.dataset?.loamEmpty) return
  if (empty.querySelector?.('.empty-state') || hasTag(empty, 'button')) return
  const p = findTag(empty, 'p')
  const text = (p?.textContent ?? empty.textContent ?? '').trim()
  if (!p || !/^no comments yet/i.test(text)) return
  const rawTog = node.querySelector?.('.dc-panel-tog')
  const tog = rawTog && !rawTog.classList?.contains?.('on') ? rawTog : null
  const canComment = Boolean(node.querySelector?.('.dc-panel-in'))
  empty.dataset.loamEmpty = '1'
  const card = tog
    ? emptyState({
        object: 'speech',
        size: 'small',
        level: 'h3',
        title: 'All caught up',
        body: 'Every comment on this file is resolved.',
        action: h('button', {
          type: 'button',
          class: 'pillbtn content-mark fs-panel-showres',
          text: tog.textContent?.trim() || 'View resolved',
          onclick: () => tog.click(),
        }),
        props: { class: 'fs-panel-empty' },
      })
    : emptyState({
        object: 'speech',
        size: 'small',
        level: 'h3',
        title: 'No comments yet',
        body: canComment ? 'Leave a note below for yourself or an agent.' : 'Notes on this file will appear here.',
        props: { class: 'fs-panel-empty' },
      })
  if (typeof empty.replaceChildren === 'function') empty.replaceChildren(card)
  else {
    while (empty.firstChild) empty.removeChild(empty.firstChild)
    empty.appendChild(card)
  }
}

export function resetPanelState(panel) {
  setPhoneSheetEscape(null)
  if (!panel) return
  panel.key = null
  panel.open = false
  panel.autoOpened = false
  panel.pendingFocus = false
  panel.returnFocus = false
  panel.lastNode = null
}

function polishPanelDom(node) {
  if (!node?.querySelector) return
  polishEmptyState(node)
  polishComposer(node)
}

function watchPanelNode(node, onChange) {
  if (!node || typeof node !== 'object') return
  const existing = observers.get(node)
  if (existing) {
    existing.onChange = onChange
    if (!existing.mutating) {
      existing.mutating = true
      try { polishPanelDom(node) } finally { existing.mutating = false }
    }
    return
  }
  const entry = { onChange, mutating: false, observer: null }
  observers.set(node, entry)
  entry.mutating = true
  try { polishPanelDom(node) } finally { entry.mutating = false }

  if (typeof MutationObserver !== 'function' || !node.nodeType) return
  entry.observer = new MutationObserver(() => {
    if (entry.mutating) return
    entry.mutating = true
    try { polishPanelDom(node) } finally { entry.mutating = false }
    entry.onChange?.()
  })
  entry.observer.observe(node, { childList: true, subtree: true })
}

let activeSheetEsc = null
function setPhoneSheetEscape(handler) {
  if (activeSheetEsc && typeof globalThis.document?.removeEventListener === 'function') {
    globalThis.document.removeEventListener('keydown', activeSheetEsc)
    activeSheetEsc = null
  }
  if (handler && typeof globalThis.document?.addEventListener === 'function') {
    activeSheetEsc = handler
    globalThis.document.addEventListener('keydown', handler)
  }
}

function afterFrame(fn) {
  if (typeof globalThis.requestAnimationFrame === 'function') globalThis.requestAnimationFrame(fn)
  else globalThis.setTimeout?.(fn, 0)
}

function applyFocus(panel, realNode) {
  if (panel.returnFocus) {
    panel.returnFocus = false
    afterFrame(() => {
      const scope = realNode?.closest?.('[data-panel], .screen') ?? globalThis.document
      const btn = scope?.querySelector?.('.fs-comments-btn')
      btn?.focus?.({ preventScroll: true })
    })
    return
  }
  if (!panel.open || !panel.pendingFocus || panel.loading) return
  panel.pendingFocus = false
  afterFrame(() => {
    if (!panel.open || !realNode?.isConnected) return
    if (panel.count === 0 && !panel.showingResolved) {
      const ta = realNode.querySelector?.('.dc-panel-in')
      if (ta && !ta.disabled) {
        ta.focus?.({ preventScroll: true })
        return
      }
    }
    const host = realNode.closest?.('.fs-side-panel, .fs-panel-sheet')
    host?.focus?.({ preventScroll: true })
  })
}

export function resolveFilePanels(files, file, options = {}) {
  const empty = { actions: [], hosted: [] }
  if (!files?.registry || !file) return empty
  const wide = Boolean(options.wide)
  const raw = files.registry.draw('files.viewer', {
    slot: 'panel',
    files,
    file,
    options: { ...options, wide: true, viewportWide: wide, hosted: true },
  })
  if (!raw.length) {
    if (!wide) setPhoneSheetEscape(null)
    return empty
  }

  const key = `${file.root}:${file.path}`
  const panel = files.st.panel ?? (files.st.panel = createPanelState())
  const readPref = () => files.panelPref?.get?.() ?? null
  const writePref = (value) => files.panelPref?.set?.(value)

  const actions = []
  const hosted = []

  for (const item of raw) {
    const info = inspectPanel(item)
    if (!info) continue
    if (!info.rawNode) {
      if (info.action) actions.push(info.action)
      continue
    }

    const index = hosted.length
    if (index === 0) {
      syncPanelState(panel, { key, wide, info, pref: readPref() })
      panel.lastNode = info.realNode
    }

    const openPanel = () => {
      panel.open = true
      panel.pendingFocus = true
      panel.returnFocus = false
      if (wide) writePref('open')
      files.schedule?.()
    }

    const closePanel = ({ returnFocus = false } = {}) => {
      if (!panel.open) return
      panel.open = false
      panel.pendingFocus = false
      panel.returnFocus = returnFocus
      if (wide) writePref('closed')
      if (!wide) setPhoneSheetEscape(null)
      files.schedule?.()
    }

    const togglePanel = () => {
      if (panel.open) closePanel({ returnFocus: false })
      else openPanel()
    }

    watchPanelNode(info.realNode, () => {
      if (files.st.file !== file) return
      const next = inspectPanel(item)
      if (!next) return
      const wasOpen = panel.open
      const wasCount = panel.count
      const wasResolved = panel.resolved
      const wasLoading = panel.loading
      const wasShowingResolved = panel.showingResolved
      syncPanelState(panel, { key, wide, info: next, pref: readPref() })
      const changed = wasOpen !== panel.open
        || wasCount !== panel.count
        || wasResolved !== panel.resolved
        || wasLoading !== panel.loading
        || wasShowingResolved !== panel.showingResolved
      if (panel.open && panel.pendingFocus && !panel.loading) {
        applyFocus(panel, info.realNode)
      }
      if (changed) files.schedule?.()
    })

    if (index === 0) {
      applyFocus(panel, info.realNode)
      if (!wide && panel.open) {
        setPhoneSheetEscape((e) => {
          if (e.key !== 'Escape' || e.defaultPrevented || !panel.open) return
          if (!info.realNode?.isConnected) { setPhoneSheetEscape(null); return }
          if (e.target?.closest?.('.dc-edit-fld')) return
          e.preventDefault()
          e.stopPropagation()
          closePanel({ returnFocus: true })
        })
      } else if (!wide) {
        setPhoneSheetEscape(null)
      }
    }

    const count = index === 0 ? panel.count : info.count
    const isOpen = index === 0 ? panel.open : true
    const title = info.title

    actions.push(
      h('button', {
        type: 'button',
        class: `pillbtn content-mark fs-comments-btn dc-pbtn${isOpen ? ' on' : ''}${count > 0 ? ' has' : ''}`,
        key: `fs:panel-toggle:${index}`,
        'aria-label': count > 0 ? `${title} (${count} open)` : title,
        'aria-pressed': wide ? String(isOpen) : null,
        'aria-expanded': String(isOpen),
        title: isOpen ? `Close ${title.toLowerCase()}` : title,
        onclick: togglePanel,
      },
        icon.comment(16),
        h('span', { class: 'fs-comments-lbl', text: title }),
        count > 0 ? h('span', { class: 'fs-comments-badge', 'aria-hidden': 'true', text: String(count) }) : null,
      ),
    )

    hosted.push({ index, title, rawNode: info.rawNode, realNode: info.realNode, open: isOpen, close: closePanel })
  }

  return { actions, hosted }
}

function gripDragHandler(close) {
  return (e) => {
    if (e.button && e.button !== 0) return
    const sheet = e.currentTarget?.closest?.('.fs-panel-sheet')
    if (!sheet) return
    const startY = e.clientY
    let dy = 0
    sheet.classList.add('dragging')
    const move = (ev) => {
      dy = Math.max(0, ev.clientY - startY)
      sheet.style.transform = dy ? `translateY(${dy}px)` : ''
    }
    const end = () => {
      sheet.classList.remove('dragging')
      sheet.style.transform = ''
      globalThis.window?.removeEventListener?.('pointermove', move)
      globalThis.window?.removeEventListener?.('pointerup', end)
      globalThis.window?.removeEventListener?.('pointercancel', end)
      if (dy > 72) close({ returnFocus: true })
    }
    globalThis.window?.addEventListener?.('pointermove', move)
    globalThis.window?.addEventListener?.('pointerup', end)
    globalThis.window?.addEventListener?.('pointercancel', end)
  }
}

export function renderHostedPanels(hosted, { wide, contentParts }) {
  if (!hosted.length) return contentParts
  const primary = hosted[0]
  const anyOpen = hosted.some(p => p.open)

  const keyHandler = (close, open) => (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented || !open) return
    if (e.target?.closest?.('.dc-edit-fld')) return
    e.preventDefault()
    e.stopPropagation()
    close({ returnFocus: true })
  }

  if (!wide) {
    return [
      ...contentParts,
      ...hosted.map((p) =>
        h('div', {
          class: `fs-panel-sheet-wrap${p.open ? ' open' : ''}`,
          key: `fs:panel-sheet:${p.index}`,
          inert: !p.open,
          'aria-hidden': p.open ? null : 'true',
          onkeydown: keyHandler(p.close, p.open),
        },
          h('div', {
            class: 'fs-panel-backdrop',
            'aria-hidden': 'true',
            onclick: () => p.close({ returnFocus: true }),
          }),
          h('div', {
            class: 'fs-panel-sheet dc-panel-dlg fs-panel-host',
            role: 'dialog',
            'aria-modal': 'true',
            'aria-label': p.title,
            tabindex: '-1',
          },
            h('div', {
              class: 'fs-panel-grip',
              'aria-hidden': 'true',
              onpointerdown: gripDragHandler(p.close),
            }, h('span')),
            h('button', {
              type: 'button',
              class: 'iconbtn ghost fs-panel-close',
              'aria-label': `Close ${p.title.toLowerCase()}`,
              title: `Close ${p.title.toLowerCase()}`,
              onclick: () => p.close({ returnFocus: true }),
            }, icon.close(14)),
            p.rawNode,
          ),
        ),
      ),
    ]
  }

  return [
    h('div', { class: `fs-split${anyOpen ? ' panel-open' : ''}`, key: 'fs:split' },
      h('div', { class: 'fs-main-col', key: 'fs:main-col' }, ...contentParts),
      h('div', {
        class: `fs-split-backdrop${anyOpen ? ' open' : ''}`,
        key: 'fs:split-backdrop',
        'aria-hidden': 'true',
        onclick: () => primary.close({ returnFocus: true }),
      }),
      ...hosted.map((p) =>
        h('aside', {
          class: `fs-side-panel fs-panel-host${p.open ? ' open' : ''}`,
          key: `fs:panel:${p.index}`,
          'aria-label': p.title,
          tabindex: '-1',
          inert: !p.open,
          'aria-hidden': p.open ? null : 'true',
          onkeydown: keyHandler(p.close, p.open),
        },
          h('div', { class: 'fs-side-panel-inner', key: `fs:panel-inner:${p.index}` },
            h('button', {
              type: 'button',
              class: 'iconbtn ghost fs-panel-close',
              'aria-label': `Close ${p.title.toLowerCase()}`,
              title: `Close ${p.title.toLowerCase()}`,
              onclick: () => p.close({ returnFocus: true }),
            }, icon.close(14)),
            p.rawNode,
          ),
        ),
      ),
    ),
  ]
}
