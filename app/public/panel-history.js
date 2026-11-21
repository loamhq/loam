import { isMac } from './keys.js'
import { h, icon } from './ui.js'

// Only entries pushed by this page count. A cold link may have another site
// behind it, so history.length cannot decide whether to show a Back button.
export function createMainHistory({ win = globalThis, guard, syncSide, itemAt, sideKey, intercept = () => null }) {
  const history = win.history
  const index = () => history?.state?.panelIndex ?? 0
  const rootIndex = () => history?.state?.panelRoot ?? index()
  let current = index(), boundary = rootIndex(), furthest = current, rollback = null, returning = null
  let requested = false
  // If an item was moved to side, visiting its old address must not steal it
  // back. Each entry knows how far back the preceding run of that item began.
  const occupied = href => href && href === sideKey()
  function backSteps() {
    const back = history?.state?.panelBack
    if (index() <= rootIndex()) return 0
    if (!back || !occupied(back.item)) return 1
    return back.first > rootIndex() ? index() - back.first + 1 : 0
  }
  const write = (data, title, url, { root = false, replace = false } = {}) => {
    const next = index() + (replace ? 0 : 1)
    const panelRoot = root ? next : rootIndex()
    let panelBack = history?.state?.panelBack
    if (root) panelBack = null
    else if (!replace) {
      const item = itemAt(win.location?.href ?? win.location?.pathname ?? '/')
      panelBack = { item, first: panelBack?.item === item ? panelBack.first : index() }
    }
    history?.[replace ? 'replaceState' : 'pushState']?.({
      ...data, panelIndex: next, panelRoot, panelBack,
      panelVisit: replace && !root ? history.state?.panelVisit : win.crypto?.randomUUID?.() ?? String(Math.random()),
    }, title, url)
    current = index()
    if (!replace) furthest = current
    boundary = rootIndex()
  }
  const push = (data, title, url, options) => write(data, title, url, options)
  const replace = (data, title, url, options) => write(data, title, url, { ...options, replace: true })
  const canForward = () => index() < furthest || Boolean(win.navigation?.canGoForward)
  const start = (data, title, url) => write(data, title, url, { root: true, replace: index() === rootIndex() && win.navigation?.canGoForward === false && !canForward() })
  const go = delta => {
    const steps = delta < 0 ? -backSteps() : delta
    if (!history?.go || !steps) return false
    requested = steps < 0 || canForward()
    history.go(steps)
    return true
  }
  // Native gestures move the browser before the app can ask about edits.
  // Return to the actual source entry while saving. Replay the traversal only
  // after both that return and the existing save/discard guard have finished.
  win.addEventListener?.('popstate', event => {
    if (!win.location?.href || !history?.go) return
    if (returning && index() === current) {
      const run = returning
      returning = null
      event.stopImmediatePropagation()
      run()
      return
    }
    if (rollback && index() === rollback.from) {
      event.stopImmediatePropagation()
      rollback.returned = true
      if (rollback.accepted) history.go(rollback.delta)
      return
    }
    const delta = index() - current
    const ownTraversal = requested || rollback?.accepted
    requested = false
    rollback = null
    if (delta) {
      const action = ownTraversal ? null : intercept(delta)
      if (action || index() < boundary) {
        // The browser already moved. Return before routing the abandoned tab
        // or disturbing a mounted pane, including an unsaved editor.
        event.stopImmediatePropagation()
        returning = action ?? (() => {})
        history.go(-delta)
        return
      }
      const destination = itemAt(win.location.href)
      if (occupied(destination)) {
        event.stopImmediatePropagation()
        const step = delta < 0 ? -backSteps() : canForward() ? 1 : 0
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
    boundary = rootIndex()
    furthest = Math.max(furthest, current)
    syncSide()
  }, true)
  return { push, replace, start, go, atCurrent: () => index() === current, canBack: () => backSteps() > 0 }
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
