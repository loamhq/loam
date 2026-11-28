import { messageSegments } from './thread-record.js'
import { stripVisualBlocks } from './visual-doc.js'

export const canReply = (message) => Boolean(message?.id && message.kind !== 'system' && !message.streaming && !message.queued && message.id !== 'pending-send')
// A quote is a glimpse of the words, read as words: its first line with the
// markdown's marks taken off (2026-11-17: `**Yes to rough edges.**` showed
// its asterisks). Nothing else of the line changes.
export function plainLine(line) {
  return line
    .replace(/^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)/, '')
    .replace(/!?\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2').replace(/!?\[\[([^\]]+)\]\]/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g, '$2').replace(/(^|[^\w*])([*_])(?=\S)(.+?)(?<=\S)\2(?![\w*])/g, '$1$3')
    .replace(/~~(?=\S)(.+?)(?<=\S)~~/g, '$1').replace(/`([^`]+)`/g, '$1')
    .trim()
}
export function quoteLine(message) {
  const raw = messageSegments(message).find((part) => part.text?.trim())?.text ?? ''
  const text = stripVisualBlocks(raw)
  const line = text.split(/\r?\n/).find((line) => line.trim() && !/^\s*(```|~~~|[-*_]{3,}\s*$)/.test(line))
  return (line && plainLine(line)) || message.attachments?.map((file) => file.name).join(', ') || 'Message'
}

// Resolve quote identities without expanding the reading window or marking an
// unseen tail read. The same pages are reused when the reader jumps/prepends.
export function replyQuotes({ read, changed, active = () => true }) {
  const messages = new Map(), pages = new Map()
  let before, ended = false, busy = false, failed = false
  const page = (id) => {
    if (!pages.has(id)) pages.set(id, Promise.resolve().then(() => read(id)).catch((error) => { pages.delete(id); throw error }))
    return pages.get(id)
  }
  return {
    page,
    get: (id) => canReply(messages.get(id)) ? messages.get(id) : null,
    async ensure(visible, hasMore) {
      for (const message of visible) if (!message.streaming) messages.set(message.id, message)
      before ??= visible.find((m) => !m.streaming)?.id
      if (!hasMore) ended = true
      if (busy || ended || failed || !before) return
      busy = true
      try {
        const wanted = () => visible.some((m) => !m.reply_source && /^m\d+$/.test(m.reply_to ?? '') && !messages.has(m.reply_to))
        while (active() && !ended && wanted()) {
          const body = await page(before)
          if (!active()) return
          for (const message of body.messages) messages.set(message.id, message)
          const first = body.messages[0]?.id
          ended = !body.has_more || !first || first === before
          before = first ?? before
          changed()
        }
      } catch { failed = true /* A failed lookup leaves an honest, unlinked quote. */ }
      finally { busy = false }
    },
  }
}

// Keep vertical scrolling, links, code/table scrolling and text selection native.
// A horizontal drag alone claims the gesture; threshold feedback is a small
// resisted snap, with an optional single device pulse where supported.
export function replyGesture({ reply, reduced = () => false }) {
  let gesture = null, suppressUntil = 0
  const selecting = () => globalThis.getSelection?.()?.isCollapsed === false
  function reset() {
    if (!gesture) return
    clearTimeout(gesture.timer)
    gesture.node.style.removeProperty('--reply-drag')
    gesture.node.classList.remove('reply-dragging', 'reply-ready')
    gesture = null
  }
  function paint() {
    const g = gesture
    if (!g?.horizontal) return
    if (g.node.isConnected === false) { reset(); return }
    g.node.classList.add('reply-dragging'); g.node.classList.toggle('reply-ready', g.ready)
    g.node.style.setProperty('--reply-drag', `${g.drag}px`)
  }
  return {
    reset, paint,
    down(event, message) {
      reset()
      if (event.pointerType !== 'touch' || event.isPrimary === false || selecting() || !canReply(message) || event.target.closest?.('button,a,input,textarea,pre,code,.tablewrap,.reply-visual,[contenteditable]')) return
      const g = gesture = { node: event.currentTarget, message, x: event.clientX, y: event.clientY, pointer: event.pointerId }
      g.timer = setTimeout(() => {
        if (gesture !== g) return
        // A hold belongs to native selection, including any later handle drag.
        reset()
      }, 480)
    },
    move(event) {
      const g = gesture
      if (!g || event.pointerId !== g.pointer) return
      if (selecting()) { reset(); return }
      const x = event.clientX - g.x, y = event.clientY - g.y
      if (Math.abs(y) > 10 && Math.abs(y) * 1.5 >= Math.abs(x)) { reset(); return }
      if (!g.horizontal && x > 12 && x > Math.abs(y) * 1.5) {
        clearTimeout(g.timer)
        g.horizontal = true; g.node.setPointerCapture?.(g.pointer)
      }
      if (!g.horizontal) return
      event.preventDefault()
      const ready = x >= 56
      if (ready && !g.pulsed) { g.pulsed = true; if (!reduced()) globalThis.navigator?.vibrate?.(8) }
      g.ready = ready
      g.drag = Math.max(0, Math.min(76, x * .68 + (ready ? 6 : 0)))
      paint()
    },
    up() {
      const g = gesture
      if (g?.horizontal) {
        suppressUntil = Date.now() + 500
        if (g.ready && !selecting()) reply(g.message)
      }
      reset()
    },
    click(event) { if (Date.now() < suppressUntil) { event.preventDefault(); event.stopPropagation() } },
  }
}
