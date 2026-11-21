import { isMac } from './keys.js'
import { h, icon } from './ui.js'

// Each visit belongs to one surface. Their order is also the system Back
// order. Removing a list's visits on tab selection leaves both panels intact.
export function createWorkspaceHistory({ win = globalThis, address, apply, guard, overlay }) {
  const history = win.history
  const saved = history?.state?.workspace
  const state = saved ?? { tab: 'chats', front: 'list', visits: [], forward: [], serial: 0 }
  let index = history?.state?.panelIndex ?? 0
  let furthest = history?.state?.furthest ?? index
  let request = null, returning = null
  const current = surface => state.visits.findLast(step => step.surface === surface)
  const next = (delta, surface) => (delta < 0 ? state.visits : state.forward).findLast(step => !surface || step.surface === surface)
  const atCurrent = () => !history?.go || index === (history.state?.panelIndex ?? 0)
  function write(push = false, href = address(state)) {
    if (push) furthest = ++index
    const main = current('main')
    history?.[push ? 'pushState' : 'replaceState']?.({
      ...main?.data, panelVisit: main?.id, panelIndex: index, furthest, workspace: state,
    }, '', href)
  }
  function visit(surface, href, data = {}, repeat = false) {
    if (surface !== 'side') state.front = surface
    if (!repeat && current(surface)?.href === href) { write(); return }
    const previous = current('main')
    if (previous && history?.state?.profileReturn) previous.data.profileReturn = history.state.profileReturn
    state.visits.push({ surface, href, data, id: ++state.serial })
    state.forward = state.forward.filter(step => step.surface !== surface)
    write(true)
  }
  function forget(predicate) {
    state.visits = state.visits.filter(step => !predicate(step))
    state.forward = state.forward.filter(step => !predicate(step))
    if (!current('main')) state.front = 'list'
  }
  function clear(surface) { forget(step => step.surface === surface) }
  function select(tab) {
    clear('list')
    state.tab = tab
    state.front = 'list'
    write()
  }
  function walk(delta, surface) {
    const source = delta < 0 ? state.visits : state.forward
    const dest = delta < 0 ? state.forward : state.visits
    const step = next(delta, surface)
    if (!step) return
    source.splice(source.indexOf(step), 1)
    dest.push(step)
    if (step.surface !== 'side') state.front = step.surface === 'main' && current('main') ? 'main' : 'list'
    if (delta < 0 && step.data.returnToList) state.front = 'list'
    return step.surface
  }
  function go(delta, surface) {
    if (!next(Math.sign(delta), surface) || request || returning) return false
    const branch = delta > 0 && index >= furthest
    if (!history?.go || branch) {
      if (guard(next(delta, surface).surface, () => go(delta, surface))) {
        const changed = walk(delta, surface)
        write(branch); apply(changed)
      }
    } else {
      request = { surface }
      history.go(delta)
    }
    return true
  }
  win.addEventListener?.('popstate', event => {
    const at = history?.state?.panelIndex ?? 0
    if (!win.location?.href || !history?.go || !history.state?.workspace) return
    if (returning && at === index) {
      event.stopImmediatePropagation()
      const done = returning; returning = null; done()
      return
    }
    const distance = at - index
    if (!distance) return
    const surface = request?.surface, delta = Math.sign(distance)
    const dismiss = !request && overlay()
    request = null
    event.stopImmediatePropagation()
    const bounce = action => { returning = action; history.go(-distance) }
    if (dismiss || !next(delta, surface)) {
      bounce(delta < 0 && dismiss ? dismiss : () => {})
      return
    }
    // A native gesture has already moved the browser. Restore the source
    // entry before a save/discard decision can replay the requested motion.
    const count = Math.abs(distance)
    const affected = (delta < 0 ? state.visits : state.forward)
      .filter(step => !surface || step.surface === surface).slice(-count)
    for (const target of new Set(affected.map(step => step.surface))) {
      let accepted = false, backAtSource = false
      const retry = () => { accepted = true; if (backAtSource) go(distance, surface) }
      if (!guard(target, retry)) {
        bounce(() => { backAtSource = true; if (accepted) go(distance, surface) })
        return
      }
    }
    const changed = new Set()
    for (let i = 0; i < count && next(delta, surface); i++) changed.add(walk(delta, surface))
    index = at
    write()
    for (const target of changed) apply(target)
  }, true)
  return { state, current, visit, forget, clear, select, go, can: (delta, surface) => Boolean(next(delta, surface)), atCurrent,
    save: () => write(), front: surface => { state.front = surface; write() },
    // Keep an app-owned entry below the root for Back on a popup at root.
    start(tab, href) { state.tab = tab; write(false, href); write(true, href) },
    restored: Boolean(saved) }
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
