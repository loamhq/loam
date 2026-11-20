import { isMac } from './keys.js'
import { h, icon } from './ui.js'

// Only entries pushed by this page count. A cold link may have another site
// behind it, so history.length cannot decide whether to show a Back button.
export function createMainHistory({ win = globalThis, guard, syncSide, itemAt, sideKey }) {
  const history = win.history
  const index = () => history?.state?.panelIndex ?? 0
  let current = index(), furthest = current, rollback = null
  // If an item was moved to side, visiting its old address must not steal it
  // back. Each entry knows how far back the preceding run of that item began.
  const occupied = href => href && href === sideKey()
  function backSteps() {
    const back = history?.state?.panelBack
    if (!back || !occupied(back.item)) return index() ? 1 : 0
    return back.first > 0 ? index() - back.first + 1 : 0
  }
  const push = (data, title, url) => {
    const next = index() + 1
    const item = itemAt(win.location?.href ?? win.location?.pathname ?? '/')
    const previous = history?.state?.panelBack
    const panelBack = { item, first: previous?.item === item ? previous.first : index() }
    history?.pushState?.({ ...data, panelIndex: next, panelBack, panelVisit: win.crypto?.randomUUID?.() ?? String(Math.random()) }, title, url)
    furthest = current = index()
  }
  const go = delta => {
    const steps = delta < 0 ? -backSteps() : delta
    if (!history?.go || !steps) return false
    history.go(steps)
    return true
  }
  // Native gestures move the browser before the app can ask about edits.
  // Return to the actual source entry while saving. Replay the traversal only
  // after both that return and the existing save/discard guard have finished.
  win.addEventListener?.('popstate', event => {
    if (!win.location?.href || !history?.go) return
    if (rollback && index() === rollback.from) {
      event.stopImmediatePropagation()
      rollback.returned = true
      if (rollback.accepted) history.go(rollback.delta)
      return
    }
    const delta = index() - current
    rollback = null
    if (delta) {
      const destination = itemAt(win.location.href)
      if (occupied(destination)) {
        event.stopImmediatePropagation()
        const step = delta < 0 ? -backSteps() : index() < furthest || win.navigation?.canGoForward ? 1 : 0
        history.go(step || -delta)
        return
      }
      const attempt = { from: current, delta }
      const retry = () => {
        attempt.accepted = true
        if (rollback === attempt && attempt.returned) history.go(delta)
      }
      if (!guard(retry)) {
        rollback = attempt
        event.stopImmediatePropagation()
        history.go(-delta)
        return
      }
    }
    current = index()
    furthest = Math.max(furthest, current)
    syncSide()
  }, true)
  return { push, go, canBack: () => backSteps() > 0 }
}

export function historyBackButton(action) {
  if (!action) return null
  const shortcut = action.shortcut ?? !action.label
  const mac = isMac(globalThis.navigator)
  return h('button', { key: 'panel-history-back', class: 'iconbtn ghost panel-history-back', type: 'button',
    title: action.title, 'aria-label': action.label ?? 'Back', 'aria-keyshortcuts': shortcut ? (mac ? 'Meta+[' : 'Control+[') : null, onclick: action.go }, icon.chevronLeft(20))
}

export function backTitle() {
  return isMac(globalThis.navigator) ? 'Back (⌘[)' : 'Back (Ctrl+[)'
}

export function historyShortcut(event, go) {
  if (event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey || !(event.metaKey || event.ctrlKey)) return
  if (event.key !== '[' && event.key !== ']') return
  if (event.target?.isContentEditable || event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]')) return
  if (go(event.key === '[' ? -1 : 1)) event.preventDefault()
}
