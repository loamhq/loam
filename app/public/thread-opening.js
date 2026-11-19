import { layoutPixels, uiScale } from './scale.js'

// Opening policy is a view of the broker's messages, never a second read mark.
export const OPENING_MESSAGES = 12
export const HISTORY_MESSAGES = 30

export function agentReply(message, agents) {
  return message.kind !== 'system' && agents.some((a) => a.id === message.author)
}

export function openingContext(messages, agents, unread = 0, firstUnseen = null) {
  const replies = messages.filter((m) => agentReply(m, agents) && !m.streaming)
  // Older brokers provide only the summary's count. Freeze it before the GET
  // clears it; a newer broker can supply the exact boundary with its page.
  const unseen = firstUnseen ?? (unread > 0 && replies.length >= unread ? replies.at(-unread)?.id : null)
  const person = messages.findLast((m) => m.kind !== 'system' && !m.streaming && !agents.some((a) => a.id === m.author))
  return { unseen, person: person?.id ?? null, needsEarlier: !person || unread > replies.length || Boolean(firstUnseen && !messages.some((m) => m.id === firstUnseen)) }
}

export function readingAnchor(pane) {
  if (!pane) return null
  const bounds = pane.getBoundingClientRect(), top = bounds.top
  const eye = top + (pane.clientHeight || 0) * uiScale() / 3
  const node = [...pane.querySelectorAll('[data-message-id]')].find((n) => n.getBoundingClientRect().bottom > eye)
  if (!node) return null
  const anchor = { id: node.getAttribute('data-message-id'), offset: layoutPixels(node.getBoundingClientRect().top - top),
    ...(node.__key ? { key: node.__key } : {}) }
  // A message can itself be thousands of pixels long. Keep the actual text
  // at eye level, so an earlier tool/image in that same reply may grow too.
  const doc = pane.ownerDocument
  const x = Math.max(bounds.left + 24, node.getBoundingClientRect().left + 24)
  let range = doc?.caretRangeFromPoint?.(x, eye)
  if (!range) {
    const point = doc?.caretPositionFromPoint?.(x, eye)
    if (point?.offsetNode) { range = doc.createRange(); range.setStart(point.offsetNode, point.offset); range.collapse(true) }
  }
  if (range?.startContainer.nodeType === 3 && node.contains(range.startContainer)) {
    const text = range.startContainer.nodeValue
    const char = Math.min(range.startOffset, Math.max(0, text.length - 1))
    const start = Math.max(text.lastIndexOf('\n', char - 1) + 1, char - 20)
    const end = Math.min(text.indexOf('\n', char) < 0 ? text.length : text.indexOf('\n', char), char + 40)
    const needle = text.slice(start, end)
    let occurrence = 0
    const walker = doc.createTreeWalker(node, 4)
    while (needle && walker.nextNode()) {
      const value = walker.currentNode.nodeValue
      for (let at = value.indexOf(needle); at >= 0; at = value.indexOf(needle, at + 1)) {
        if (walker.currentNode === range.startContainer && at >= start) break
        occurrence++
      }
      if (walker.currentNode === range.startContainer) break
    }
    range.setStart(range.startContainer, char); range.setEnd(range.startContainer, char + 1)
    const rect = range.getBoundingClientRect()
    if (rect.height && needle.trim()) Object.assign(anchor, { text: needle, char: char - start, occurrence, textOffset: layoutPixels(rect.top - top) })
  }
  return anchor
}

export function restoreAnchor(pane, anchor) {
  const node = anchor && [...pane.querySelectorAll('[data-message-id]')].find((n) => n.getAttribute('data-message-id') === anchor.id || (anchor.key && n.__key === anchor.key))
  if (!node) return false
  if (anchor.text && node.ownerDocument?.createTreeWalker) {
    const walker = node.ownerDocument.createTreeWalker(node, 4)
    let occurrence = 0
    while (walker.nextNode()) {
      const value = walker.currentNode.nodeValue
      for (let at = value.indexOf(anchor.text); at >= 0; at = value.indexOf(anchor.text, at + 1)) {
        if (occurrence++ !== (anchor.occurrence ?? 0)) continue
        const range = node.ownerDocument.createRange()
        range.setStart(walker.currentNode, at + anchor.char); range.setEnd(walker.currentNode, at + anchor.char + 1)
        pane.scrollTop += layoutPixels(range.getBoundingClientRect().top - pane.getBoundingClientRect().top) - anchor.textOffset
        return true
      }
    }
  }
  pane.scrollTop += layoutPixels(node.getBoundingClientRect().top - pane.getBoundingClientRect().top) - anchor.offset
  return true
}

// A gap page can overlap the saved window AND extend above it. Join at the
// overlap instead of inserting every new ID at the gap's lower edge.
export function mergeHistory(messages, page, before = null) {
  const known = new Map(messages.map((m) => [m.id, m]))
  const first = page.findIndex((m) => known.has(m.id))
  if (first >= 0) {
    const last = page.findLastIndex((m) => known.has(m.id))
    const start = messages.findIndex((m) => m.id === page[first].id)
    const end = messages.findIndex((m) => m.id === page[last].id)
    return { messages: [...messages.slice(0, start), ...page.map((m) => known.get(m.id) ?? m), ...messages.slice(end + 1)], connected: true, extendsStart: start === 0 }
  }
  const at = before ? messages.findIndex((m) => m.id === before) : 0
  return { messages: [...messages.slice(0, Math.max(0, at)), ...page, ...messages.slice(Math.max(0, at))], connected: false, extendsStart: !before }
}

export function threadHref(id) { return `/threads/${encodeURIComponent(id)}` }
export function threadFromPath(pathname) {
  const match = /^\/threads\/([^/]+)\/?$/.exec(pathname)
  try { return match ? decodeURIComponent(match[1]) : null } catch { return null }
}

// Text paints first. Only prose entering the reading window is formatted, one
// block per task after a paint; cached kept nodes retain copy/link handlers.
export function progressiveProse({ build, plain, changed, cache = new Map(), Observer = globalThis.IntersectionObserver,
  defer = (fn) => requestAnimationFrame(() => setTimeout(fn, 0)) }) {
  const entries = cache, nodes = new Set(), queue = []
  for (const entry of entries.values()) if (!entry.ready) entry.queued = false
  let observer, pane, scheduled = false, stopped = false
  function pump() {
    if (scheduled || stopped || !queue.length) return
    scheduled = true
    defer(() => {
      scheduled = false
      if (stopped) return
      const entry = queue.pop()
      if (entries.get(entry.key) === entry) { changed(() => { entry.node = build(entry.text, entry.context); entry.ready = true }) }
      pump()
    })
  }
  function ensure(entry) {
    if (!entry || entry.queued || entry.ready) return
    entry.queued = true; queue.push(entry); pump()
  }
  return {
    cache,
    // `context` (2026-11-18: a reply's pictures) goes to build and plain; a
    // different `context.signature` builds the node again.
    node(text, key, context = null) {
      let entry = entries.get(key)
      const signature = context?.signature ?? null
      if (!entry || entry.text !== text || entry.signature !== signature) {
        entry = { key, text, signature, context, ready: !Observer, node: Observer ? plain(text, key, context) : build(text, context) }
        entries.set(key, entry)
      }
      return entry.node
    },
    watch(next) {
      if (stopped) return
      if (pane !== next) { observer?.disconnect(); nodes.clear(); pane = next; observer = null }
      if (!pane || !Observer) return
      observer ??= new Observer((items) => { for (const item of items) if (item.isIntersecting) ensure(entries.get(item.target.getAttribute('data-prose-pending'))) }, { root: pane, rootMargin: '160px 0px' })
      const current = new Set(pane.querySelectorAll('[data-prose-pending]'))
      for (const node of nodes) if (!current.has(node)) { observer.unobserve(node); nodes.delete(node) }
      for (const node of current) if (!nodes.has(node)) { nodes.add(node); observer.observe(node) }
    },
    stop() { stopped = true; queue.length = 0; observer?.disconnect(); nodes.clear() },
  }
}
