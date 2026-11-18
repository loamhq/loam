import { isImage, attachmentFilePath } from './attach.js'
import { openImageView } from './image-view.js'
import { h, kept, blob, avatarLook, icon } from './ui.js'

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
  return h('div', { class: wide ? 'chat-head' : 'thread-head', ...props }, !wide ? back : null,
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
export function messageFiles(files = []) {
  if (!files?.length) return null
  const signature = JSON.stringify(files.map(({ path, name, type, root }) => [path, name, type, root]))
  const cached = fileViews.get(files)
  if (cached?.signature === signature) return kept(cached.view)
  // The images, in order, for the full view a tap opens (2026-11-18). The
  // owner's files are the vault's; an agent's may be a workspace's (`root`).
  const shown = [], failed = new Set()
  const view = h('div', { class: 'bubble-files' }, ...files.map((file, i) => {
    const name = file.name ?? file.path
    // Only host-stored paths are read, through the existing authenticated,
    // inert Files download route. Never treat an attachment path as a URL.
    const path = attachmentFilePath(file.path)
    const image = isImage(file.type) && path !== null
    if (!image) return h('span', { class: 'attachment-chip sent', key: `f${i}`, title: file.path ?? '' },
      isImage(file.type) ? icon.image(13) : icon.file(13), h('span', { class: 'chip-name', text: name }))
    const root = file.root === 'spaces' ? 'spaces' : 'vault'
    const href = `/api/files/download?${new URLSearchParams({ root, path })}`
    const at = shown.push({ src: href, href, name }) - 1
    const label = h('span', { class: 'message-image-name', text: name })
    const link = h('a', { class: 'message-image', key: `f${i}`, href, title: name, 'aria-label': `Open ${name}`, onclick: (e) => {
      // A plain tap opens it full size; a modified click keeps the browser's own.
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button > 0) return
      if (link.className !== 'message-image') return
      e.preventDefault()
      e.stopPropagation()
      openImageView(shown.filter((_, j) => !failed.has(j)), shown.slice(0, at + 1).filter((_, j) => !failed.has(j)).length - 1)
    } },
      h('img', { src: href, alt: file.alt ?? '', loading: 'lazy', decoding: 'async', onerror: (e) => {
        failed.add(at)
        e.target.hidden = true
        link.className = 'attachment-chip sent'
        label.className = 'chip-name'
      } }), label)
    return link
  }))
  fileViews.set(files, { signature, view })
  return kept(view)
}

export function selectionMark({ kind = 'checkbox', chosen = false } = {}) {
  return h('span', { class: `qmark ${kind}${chosen ? ' on' : ''}`, 'aria-hidden': 'true' }, chosen ? icon.check(12) : null)
}



export function sheetHandle(close) {
  return h('div', { class: 'sheet-top' },
    h('div', { class: 'grip', 'aria-hidden': 'true' }),
    h('button', { class: 'iconbtn ghost sheet-x', type: 'button', title: 'Close', 'aria-label': 'Close', onclick: close }, icon.close(14)))
}
