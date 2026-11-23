import { hasPlacesGreeting, placesImage, placesDiagram } from './onboarding.js'
import { isImage, attachmentFilePath } from './attach.js'
import { openImageView } from './image-view.js'
import { h, kept, blob, avatarLook, icon } from './ui.js'
import { imageRefs } from './markdown.js'

// Rendering primitives shared by threads and page panels.
// State and protocol adapters stay with their respective screens.

export function avatar(view, { size = 52, font = 23 } = {}) {
  const paint = avatarLook(view)
  view = { ...paint, ring: view.ring === view.color ? paint.color : view.ring }
  return h(
    'div',
    { class: 'avatar-wrap' },
    // Working — and waiting, since 2026-10-02 — is two breathing copies of the
    // blob behind it: the session is active and more output is coming. A
    // working session silent past the quiet threshold does not breathe: it is
    // not mid-sentence.
    view.working
      ? [
          h('div', { key: 'breathe', class: 'breathe', style: { borderRadius: view.shape, background: view.color } }),
          h('div', { key: 'breathe-late', class: 'breathe late', style: { borderRadius: view.shape, background: view.color } }),
        ]
      : null,
    // The ring: a gap the colour of the surface under it, then the state's own
    // colour. It is behind the blob and exactly its size and shape, so what
    // shows of it is the shadow outside the blob's edge.
    view.ring
      ? h('div', {
          key: 'ring',
          class: 'ring',
          style: { borderRadius: view.shape, boxShadow: `0 0 0 3px ${view.ringGap}, 0 0 0 5px ${view.ring}` },
        })
      : null,
    view.mark ?? blob(view, { key: 'blob', size, font }),
    view.bang ? h('div', { key: 'bang', class: 'bang', style: { borderColor: view.ringGap }, text: '!' }) : null,
  )
}





export function modelPill({ label, on = false, title, disabled = false, onclick, props = {}, lead = null }) {
  return h(
    'button',
    { class: `modelbtn${on ? ' on' : ''}`, type: 'button', title, disabled, onclick, ...props },
    lead,
    h('span', { class: 'modelbtn-name', text: label }),
    icon.chevronDown(10),
  )
}


export function settingsRow({ lead, title, sub, right = null, onclick, key = null, dot = false, as = 'button', props = {} }) {
  return h(
    as,
    { class: 'setrow', key, onclick, ...props },
    lead,
    h('div', { class: 'setrow-who' }, h('div', { class: 'setrow-title', text: title }), sub != null && sub !== '' ? h('div', { class: 'setrow-sub' }, sub) : null),
    dot ? h('span', { class: 'navdot', title: 'Needs you' }) : null,
    right,
    icon.chevronRight(10),
  )
}


export function listHeading({ wide = false, title, leading = null, actions = [], props = {} }) {
  return h('div', { class: wide ? 'list-head' : 'home-head', ...props }, leading, h('div', { class: 'display', text: title }), ...actions)
}

export function personaGrid({ agents, loading = false, wide = false, look, onclick, oncontextmenu, disabled = false, label = null, buttonProps = () => ({}), props = {} }) {
  return h('div', { class: 'strip', ...props }, agents.length ? agents.map((agent) => h('button', {
    class: 'persona', key: agent.id, disabled, title: label ? label(agent) : `New chat with ${agent.name}`, 'aria-label': label ? label(agent) : null, onclick: () => onclick(agent),
    oncontextmenu: oncontextmenu ? (e) => { e.preventDefault(); oncontextmenu(agent) } : null,
    ...buttonProps(agent),
  }, blob(look(agent.id), wide ? { size: 50, font: 21 } : { size: 60, font: 24 }), h('span', { class: 'persona-name', text: agent.name }))) : loading ? null : h('div', { class: 'muted', text: 'No agents yet.' }))
}

export function rowTop({ face, title, subtitle, meta, lead = null, extra = null }) {
  return h('div', { class: 'srow-top' }, lead, face,
    h('div', { class: 'srow-who' }, h('div', { class: 'srow-title', text: title ?? '' }), subtitle != null || extra ? h('div', { class: 'srow-line' }, h('span', { class: 'srow-name', text: subtitle }), extra) : null),
    h('div', { class: 'srow-meta' }, ...meta))
}

export function chatHeader({ wide, back, face, identity, onIdentity, identityTitle, title, subtitle,
  onRename, renameLabel = 'Rename this chat', titleField = null, draft = false, untitled = false, actions = [], props = {}, titleProps = {} }) {
  const editable = onRename && !draft
  const titleNode = titleField ? wide ? h('div', { class: 'titlefield' }, titleField) : h('div', { class: 'headtitle editing' }, h('div', { class: 'titlefield' }, titleField))
    : h(editable ? 'button' : 'div', { class: wide ? `chattitle${draft || untitled ? ' untitled' : ''}` : `headtitle${draft || untitled ? ' untitled' : ''}`, type: editable ? 'button' : null, title: editable ? renameLabel : null, onclick: editable ? onRename : null, ...titleProps },
      h('span', { class: 'headtitle-text' },
        h('span', { class: wide ? 'display' : 'display headtitle-name', text: title }),
        subtitle ? h('span', { class: 'headtitle-who', text: subtitle }) : null),
      wide && editable ? icon.pencil(13) : null)
  return h('div', { class: wide ? 'chat-head' : 'thread-head', ...props }, back,
    h('button', { class: wide ? 'agentbtn' : 'who', title: identityTitle, onclick: onIdentity }, face,
      wide && identity ? [h('span', { class: 'display', text: identity }), icon.chevronDown(12)] : null),
    wide && identity ? h('span', { class: 'sep', text: '/' }) : null,
    titleNode, wide ? h('div', { class: 'headspace' }) : null, ...actions)
}

export function chatFrame({ wide, props = {}, children = [] }) {
  return h(wide ? 'div' : 'section', { ...props, class: `${wide ? 'pane-body' : 'screen thread'} ${props.class ?? ''}`.trim() }, ...children)
}
export function chatDock(composer, hint = null, props = {}) {
  return h('div', { class: 'thread-dock', key: 'dock', ...props }, composer, hint ? h('div', { class: 'hint', text: hint }) : null)
}
export function agentProse(nodes, props = {}) { return h('div', { class: 'say agent md', ...props }, ...nodes) }
export function userBubble(props = {}, ...nodes) { return h('div', { ...props, class: [...new Set(['say', 'user', 'md', ...(props.class ?? '').split(/\s+/)])].filter(Boolean).join(' ') }, ...nodes) }
export function stepFold({ key, open, facts, live, toggle, body, props = {} }) {
  return h('div', { class: 'line left', key, ...props }, h('div', { class: 'fold' },
    h('button', { class: 'fold-head', type: 'button', 'aria-expanded': String(open), onclick: toggle },
      facts.running ? h('span', { class: 'spinner' }) : icon.check(12),
      h('b', { class: live ? 'live' : null, text: live ?? `${facts.count} ${facts.count === 1 ? 'step' : 'steps'}` }),
      facts.failed > 0 ? h('span', { class: 'failed', text: `· ${facts.failed} failed` }) : null,
      h('span', { class: 'chev' }, icon.chevronDown(12))),
    open ? h('div', { class: 'fold-body' }, ...(typeof body === 'function' ? body() : body)) : null))
}
export function thinkingLine({ text, key, out = false, props = {} }) {
  return h('div', { class: 'line left', key, ...props }, h('div', { class: 'awaiting', role: 'status' }, h('span', { class: 'awaiting-dot' }), h('span', { class: `awaiting-words fade${out ? ' out' : ''}`, text })))
}
export function panelHeader({ title, back = null, actions = [], props = {} }) {
  return h('div', { class: 'chat-head plain', ...props }, back, title ? h('div', { class: 'sheet-head' }, h('h2', { class: 'display sheet-title', tabindex: '-1', text: title })) : null, h('div', { class: 'headspace' }), ...actions)
}


export function emptyConversation({ title, body, face = null, props = {} }) {
  return h('div', { class: 'pane-nothing', ...props }, face, h('h2', { class: 'display', text: title }), h('div', { class: 'why', text: body }))
}
export function composerHint({ wide, chord, thread = false, agent }) {
  return [wide ? chord : null, thread ? null : `${agent} asks before anything irreversible`].filter(Boolean).join(' · ')
}

// Expansion state belongs to the reader; both message adapters use the same sets.
export function createMessageState() { return { long: new Set(), open: new Set() } }

export function settingsNav({ title, items, active, choose, footer = null, props = {} }) {
  return h('div', { class: 'setnav', ...props }, title ? h('div', { class: 'display setnav-title', text: title }) : null,
    ...items.map((item) => h('button', { key: item.id, class: `navrow${item.id === active ? ' on' : ''}`, onclick: () => choose(item) },
      h('div', { class: 'icon-tile icon-tile-small' }, item.glyph), h('span', { text: item.label }), item.dot ? h('span', { class: 'navdot', title: 'Needs you' }) : null)), footer)
}

export function segmentedControl({ label, options, value, choose, props = {} }) {
  return h('div', { ...props, class: `segmented ${props.class ?? ''}`.trim(), role: 'group', 'aria-label': label },
    ...options.map(option => h('button', { key: option.id, type: 'button', class: option.id === value ? 'on' : '',
      'aria-pressed': String(option.id === value), disabled: option.disabled, text: option.label ?? option.name,
      onclick: () => choose(option.id) })))
}

// Retain the gallery, including each image's load/failure state. Building img
// nodes with src on every redraw can start detached requests; reconciling a
// hidden failed image also used to reveal it and restart the lazy load.
// Weak keys release the DOM when its attachment record is no longer retained.
const fileViews = new WeakMap()
let mediaViewSerial = 0
function imageMissing() {
  return h('span', { class: 'reply-image-missing' }, icon.image(16),
    h('span', { text: 'Image no longer available' }))
}
// A thread read again hands over a new attachments array for the same
// message (2026-11-18): with `key` (the message's own), the gallery is found
// by message and contents, so the pictures are not rebuilt and do not start
// loading again. Bounded: the oldest are forgotten first.
const keyedViews = new Map(), KEYED_MAX = 300
export function messageFiles(files = [], key = null) {
  if (!files?.length) return null
  const signature = JSON.stringify(files.map(({ path, name, type, root, alt, missing }) => [path, name, type, root, alt, missing]))
  const cached = fileViews.get(files) ?? (key !== null ? keyedViews.get(key) : undefined)
  if (cached?.signature === signature) { fileViews.set(files, cached); return kept(cached.view) }
  // The images, in order, for the full view a tap opens (2026-11-18). The
  // owner's files are the vault's; an agent's may be a workspace's (`root`).
  const shown = [], failed = new Set()
  const view = h('div', { class: 'bubble-files', key: `files:${++mediaViewSerial}` }, ...files.map((file, i) => {
    const name = file.name ?? file.path
    // Only host-stored paths are read, through the existing authenticated,
    // inert Files download route. Never treat an attachment path as a URL.
    const path = attachmentFilePath(file.path)
    const image = isImage(file.type)
    const root = file.root === 'spaces' ? 'spaces' : 'vault'
    const href = `/api/files/download?${new URLSearchParams({ root, path })}`
    if (path && file.type?.startsWith('video/')) {
      return h('figure', { class: 'message-video', key: `f${i}` },
        h('video', { src: href, controls: true, playsinline: true, preload: 'metadata', 'aria-label': name }),
        h('figcaption', {}, h('a', { href, download: name, text: name })))
    }
    if (!image) return h('span', { class: 'attachment-chip sent', key: `f${i}`, title: file.path ?? '' },
      icon.file(13), h('span', { class: 'chip-name', text: name }))
    const words = file.alt || name
    const loadable = path !== null && !file.missing
    const at = loadable ? shown.push({ src: href, href, name }) - 1 : -1
    const link = h('a', { class: `message-image${loadable ? '' : ' missing'}`, key: `f${i}`, href: loadable ? href : null,
      title: name, 'aria-label': loadable ? `Open ${words}` : null, onclick: (e) => {
      // A plain tap opens it full size; a modified click keeps the browser's own.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return
      if (!link.hasAttribute('href')) return
      e.preventDefault()
      e.stopPropagation()
      openImageView(shown.filter((_, j) => !failed.has(j)), shown.slice(0, at + 1).filter((_, j) => !failed.has(j)).length - 1)
    } },
      loadable ? h('img', { src: href, alt: file.alt ?? '', loading: 'lazy', decoding: 'async', onerror: () => {
        failed.add(at)
        mark(link, 'missing')
        link.removeAttribute('href')
        link.removeAttribute('aria-label')
      } }) : null, imageMissing(), h('span', { class: 'message-image-name', text: words }))
    return link
  }))
  fileViews.set(files, { signature, view })
  if (key !== null) {
    keyedViews.delete(key); keyedViews.set(key, { signature, view })
    if (keyedViews.size > KEYED_MAX) keyedViews.delete(keyedViews.keys().next().value)
  }
  return kept(view)
}

// An agent's images, drawn where it wrote them (2026-11-18, evening).
//
// The reply keeps its `![a few words](path)` lines; the host checked each
// file and put it on the message with `refs`, the destinations as written
// that name it. A reference whose destination is one of those draws its
// picture in place, through the same Files download route the owner's files
// use; any other reference is text, as it was written. A message from the
// morning before has attachments without `refs` and text the images were
// taken off: its pictures stay under its words, and a reference to one in
// its full record (the raw events still have them) draws nothing.
//
// `key` is the message's own, so a thread read again (new attachment
// objects, same files) keeps the same picture nodes and they do not load
// again. While a reply streams the host has not named its files yet: a
// finished reference to a local picture keeps its place as a quiet empty
// frame, and an unfinished one at the end is held back, not shown as marks.
const figureViews = new Map(), FIGURES_MAX = 300
// Inline reply figure layout bounds (2026-11-23):
// - Ultra-tall scrolling captures (< 9:20) frame at 0.45 top-aligned so they
//   stay readable on desktop without becoming a full-height poster; tap opens
//   the full image. Standard phone screenshots (>= 0.45) are uncropped.
// - Multi-image rows split onto a new row whenever adding the next image would
//   squeeze any item below 190px in the 760px desktop column.
const MIN_FRAME_RATIO = 0.45
const PORTRAIT_RATIO = 0.85
const ROW_COLUMN_PX = 760
const ROW_GAP_PX = 10
const ROW_MIN_ITEM_PX = 190
const ROW_LANDSCAPE_H = 440
const ROW_PORTRAIT_H = 520
const frameRatio = (ratio) => Math.max(ratio ?? 4 / 3, MIN_FRAME_RATIO)
const mark = (node, name) => { const now = node.getAttribute('class') ?? ''; if (!now.split(/\s+/).includes(name)) node.setAttribute('class', `${now} ${name}`.trim()) }
const toggleMark = (node, name, on) => {
  const parts = (node.getAttribute('class') ?? '').split(/\s+/).filter(Boolean)
  const has = parts.includes(name)
  if (on && !has) node.setAttribute('class', [...parts, name].join(' '))
  else if (!on && has) node.setAttribute('class', parts.filter((c) => c !== name).join(' '))
}
function syncRow(row) {
  if (!row || !(row.getAttribute?.('class') ?? '').split(/\s+/).includes('reply-figures-row')) return
  const figures = [...(row.childNodes ?? [])].filter((c) => c.nodeType === 1 && (c.getAttribute?.('class') ?? '').split(/\s+/).includes('reply-figure'))
  if (!figures.length) return
  const ratios = figures.map((f) => {
    const raw = parseFloat(f.style?.getPropertyValue?.('--frame-ratio') || f.style?.getPropertyValue?.('--ratio'))
    return Number.isFinite(raw) && raw > 0 ? Math.max(raw, MIN_FRAME_RATIO) : 4 / 3
  })
  row.style.setProperty('--ratio-sum', ratios.reduce((a, b) => a + b, 0).toFixed(4))
  row.style.setProperty('--count', String(figures.length))
  row.setAttribute('data-count', String(figures.length))
  toggleMark(row, 'portrait-row', ratios.every((r) => r < PORTRAIT_RATIO))
}
function rowFits(items) {
  if (items.length <= 1) return true
  const ratios = items.map((node) => frameRatio(node.__ratio))
  const sum = ratios.reduce((a, b) => a + b, 0)
  const maxH = ratios.every((r) => r < PORTRAIT_RATIO) ? ROW_PORTRAIT_H : ROW_LANDSCAPE_H
  const avail = ROW_COLUMN_PX - (items.length - 1) * ROW_GAP_PX
  const h = Math.min(maxH, avail / sum)
  return Math.min(...ratios) * h >= ROW_MIN_ITEM_PX
}
function packFigureRows(nodes) {
  const rows = []
  for (const node of nodes) {
    const current = rows[rows.length - 1]
    if (current && rowFits([...current, node])) current.push(node)
    else rows.push([node])
  }
  return rows
}
const LOCAL_PICTURE = /^<?\s*(?![a-z][a-z0-9+.-]*:(?!\/\/\/))[^<>]*\.(png|jpe?g|webp|gif|avif)\s*>?(\s+"[^"]*")?$/i
const PICTURE_REF = /!\[([^\]\n]{0,300})\]\(\s*(<[^>\n]+>|[^)\n]+?)\s*\)/g
const refPath = (dest) => {
  let path = String(dest ?? '').trim()
  if (path.startsWith('<') && path.endsWith('>')) path = path.slice(1, -1).trim()
  else path = path.replace(/\s+"[^"]*"$/, '')
  if (/^file:\/\//i.test(path)) path = path.slice(7)
  try { path = decodeURIComponent(path) } catch { /* as written */ }
  return path
}
const volatilePicture = (dest) => {
  const path = refPath(dest)
  return LOCAL_PICTURE.test(path) &&
    (path.startsWith('/tmp/') || path.startsWith('/var/lib/loam/browser/') || /^\/[^\n]*\/jobs\/\d{8}-\d{6}-[a-f0-9]{8}\/captures\//.test(path))
}
export function replyImages(message, key = null, { streaming = false } = {}) {
  const presetImage = hasPlacesGreeting(message)
  const all = (message?.attachments ?? []).filter((file) => file && typeof file === 'object')
  const pictures = all.filter((file) => isImage(file.type))
  const placed = pictures.some((file) => Array.isArray(file.refs))
  const find = (dest) => {
    const written = String(dest ?? '').trim()
    if (placed) return pictures.find((file) => file.refs?.includes(written)) ?? null
    const path = refPath(written)
    return pictures.find((file) => path === file.path || path.endsWith(`/${file.path}`)) ?? null
  }
  const src = (file) => {
    const path = attachmentFilePath(file.path)
    return file.missing || path === null ? null : `/api/files/download?${new URLSearchParams({ root: file.root === 'spaces' ? 'spaces' : 'vault', path })}`
  }
  const failed = new Set()
  const open = (file) => {
    const available = pictures.filter((f) => src(f) && !failed.has(f)), at = available.indexOf(file)
    if (at >= 0) openImageView(available.map((f) => ({ src: src(f), href: src(f), name: f.alt || f.name || '' })), at)
  }
  function figure(file, alt) {
    const ratio = file.width > 0 && file.height > 0 ? file.width / file.height : null
    const words = String(alt || file.alt || '').trim()
    const href = src(file)
    const cacheKey = key === null ? null : `${key}|${file.root}|${file.path}|${words}`
    const signature = JSON.stringify([src(file), file.width, file.height, words])
    const had = cacheKey === null ? null : figureViews.get(cacheKey)
    if (had?.signature === signature) { had.open = open; had.failed = failed; had.file = file; if (had.missing) failed.add(file); return had.stub }
    const record = { signature, open, failed, file, missing: !href }
    const framed = frameRatio(ratio)
    // Insert the kept figure itself when it takes the streaming frame's place.
    const node = h('figure', { key: `figure:${++mediaViewSerial}`, class: `reply-figure${href ? '' : ' missing known-missing'}`, style: { '--ratio': ratio ? ratio.toFixed(4) : '1.3333', '--frame-ratio': framed.toFixed(4) } })
    const view = h(href ? 'a' : 'span', { class: 'reply-image', href, 'aria-label': href ? (words ? `Open image: ${words}` : 'Open image') : null, onclick: (e) => {
      // A plain tap opens it full size; a modified click keeps the browser's own.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return
      e.preventDefault()
      e.stopPropagation()
      if (!record.missing) record.open(record.file)
    } },
    href ? h('img', { src: href, alt: words, width: file.width > 0 ? String(file.width) : null, height: file.height > 0 ? String(file.height) : null,
      loading: 'lazy', decoding: 'async',
      onload: (e) => {
        const img = e.target
        if (!ratio && img.naturalWidth > 0 && img.naturalHeight > 0) {
          const actual = img.naturalWidth / img.naturalHeight
          const next = frameRatio(actual)
          node.style.setProperty('--ratio', actual.toFixed(4))
          node.style.setProperty('--frame-ratio', next.toFixed(4))
          record.stub.__ratio = next
          syncRow(node.parentNode)
        }
        mark(node, 'loaded')
      },
      onerror: () => {
        record.missing = true
        record.failed.add(record.file)
        mark(node, 'missing')
        view.removeAttribute('href')
        view.removeAttribute('aria-label')
      } }) : null,
    imageMissing())
    node.append(view)
    if (words) node.append(h('figcaption', { class: 'reply-caption', text: words }))
    record.stub = kept(node)
    record.stub.__ratio = framed
    if (cacheKey !== null) {
      figureViews.delete(cacheKey); figureViews.set(cacheKey, record)
      if (figureViews.size > FIGURES_MAX) figureViews.delete(figureViews.keys().next().value)
    }
    return record.stub
  }
  function waiting() {
    const node = h('figure', { class: 'reply-figure waiting', style: { '--ratio': '1.3333', '--frame-ratio': '1.3333' }, 'aria-hidden': 'true' }, h('span', { class: 'reply-image' }))
    node.__ratio = 4 / 3
    return node
  }
  const missingReference = (dest) => !streaming && pictures.length < 3 && volatilePicture(dest)
  const images = {
    draw(alt, dest) {
      if (presetImage && dest === placesImage) return placesDiagram(alt)
      const file = find(dest)
      if (file) return Array.isArray(file.refs) ? figure(file, alt) : false
      if (streaming && LOCAL_PICTURE.test(String(dest ?? '').trim())) return waiting()
      return missingReference(dest) ? figure({ path: refPath(dest), missing: true }, alt) : null
    },
    group(nodes) {
      if (presetImage && nodes.length === 1 && nodes[0].getAttribute?.('class') === 'hello-places') return nodes[0]
      const rows = packFigureRows(nodes).map((row) => {
        const ratios = row.map((node) => frameRatio(node.__ratio))
        const allPortrait = ratios.every((r) => r < PORTRAIT_RATIO)
        return h('div', {
          class: `reply-figures-row${allPortrait ? ' portrait-row' : ''}`,
          'data-count': String(row.length),
          style: { '--ratio-sum': ratios.reduce((a, b) => a + b, 0).toFixed(4), '--count': String(row.length) },
        }, ...row)
      })
      return h('div', { class: 'reply-figures', 'data-count': String(nodes.length) }, ...rows)
    },
  }
  return {
    images,
    streaming,
    signature: JSON.stringify([placed, streaming, pictures.map((f) => [f.root, f.path, f.refs, f.width, f.height, f.alt, f.missing])]),
    // The attachments that have no place in these texts: drawn under the
    // words, as every attachment was before.
    rest(texts) {
      if (!placed) return all
      const dests = new Set(texts.flatMap((text) => imageRefs(text ?? '').map((ref) => ref.dest.trim())))
      return all.filter((file) => !pictures.includes(file) || !file.refs?.some((ref) => dests.has(ref)))
    },
    // The text as words: what Copy takes and a plain placeholder shows.
    // A picture's reference goes; one that is only text stays.
    words(text) {
      if (typeof text !== 'string' || !text.includes('![')) return text
      const out = text.replace(PICTURE_REF, (raw, alt, dest) => (presetImage && dest === placesImage) || find(dest) || missingReference(dest) || (streaming && LOCAL_PICTURE.test(dest.trim())) ? '\u0000' : raw)
      if (!out.includes('\u0000')) return text
      return out.split('\n').filter((line) => line.replace(/\u0000/g, '').trim() || !line.includes('\u0000'))
        .map((line) => line.replace(/[ \t]?\u0000/g, '')).join('\n').replace(/\n{3,}/g, '\n\n').trim()
    },
    // While it streams: an unfinished `![…` at the very end waits for the rest.
    live(text) {
      if (!streaming || typeof text !== 'string') return text
      return text.replace(/!\[[^\]\n]{0,300}(\](\([^)\n]*)?)?$/, '')
    },
  }
}

export function selectionMark({ kind = 'checkbox', chosen = false } = {}) {
  return h('span', { class: `qmark ${kind}${chosen ? ' on' : ''}`, 'aria-hidden': 'true' }, chosen ? icon.check(12) : null)
}



export function sheetHandle(close) {
  return h('div', { class: 'sheet-top' },
    h('div', { class: 'grip', 'aria-hidden': 'true' }),
    h('button', { class: 'iconbtn ghost sheet-x', type: 'button', title: 'Close', 'aria-label': 'Close', onclick: close }, icon.close(14)))
}
