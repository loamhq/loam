import { icon, blob, avatarLook } from './ui.js'

// Persist the glyph's name through the existing string-valued icon field.
// Unrecognised values remain literal text, including previously chosen emoji.
export const threadIcons = [
  [null, 'Group', 'users'], ['glyph:chat', 'Conversation', 'chat'],
  ['glyph:folder', 'Folder', 'folder'], ['glyph:list', 'List', 'list'],
  ['glyph:code', 'Code', 'code'], ['glyph:pencil', 'Writing', 'pencil'],
]
export const LISTENING_INSET = 44
export function listeningInset(visible, previous = 0, atEnd = true) {
  // Reclaiming space just above the end would clamp scrollTop and move prose.
  // Keep that invisible space until the reader returns to the end themselves.
  return visible ? LISTENING_INSET : atEnd === false ? previous : 0
}
export function threadGlyph(value, size = 20) {
  const choice = threadIcons.find(([id]) => id === (value ?? null))
  return choice?.[2] ? icon[choice[2]](size) : value ?? icon.users(size)
}

export function threadPersona(thread) {
  return !thread.icon && !thread.general && thread.id !== 'general'
    && thread.members.length === 1 && thread.members[0].persona === thread.default ? thread.default : null
}

// All names come from the roster. Explicit working:false outranks the older
// busy alias, which also covers time parked on background work.
export function threadActivity(thread, { pending = [], resident = () => null } = {}) {
  const groups = { asking: [], working: [], waiting: [] }
  for (const member of thread.members) {
    const live = resident(member.persona)
    const asking = member.asking || member.pending?.length || member.pending_permissions?.length
      || pending.some((p) => p.persona === member.persona) || live?.state === 'waiting'
    const state = asking ? 'asking' : member.working === true ? 'working'
      : member.background > 0 ? 'waiting' : member.working === false ? null
        : member.busy || member.running ? 'working' : null
    if (state) groups[state].push(member)
  }
  return groups
}

export function threadSubtitle(thread, name, options = {}) {
  const groups = threadActivity(thread, options)
  const isAgent = options.isAgent ?? ((id) => thread.members.some((m) => m.persona === id))
  const names = (members) => members.length > 2 ? `${name(members[0].persona)} +${members.length - 1}`
    : members.map((m) => name(m.persona)).join(' and ')
  if (groups.asking.length) return `${names(groups.asking)} ${groups.asking.length === 1 ? 'is' : 'are'} asking you`
  if (groups.working.length && groups.waiting.length) return `${names(groups.working)} working · ${names(groups.waiting)} waiting`
  if (groups.working.length) return `${names(groups.working)} working`
  if (groups.waiting.length) return `${names(groups.waiting)} waiting`
  return thread.unread > 0 && thread.last?.author && thread.last.kind !== 'system' && thread.last.author !== 'system'
    && isAgent(thread.last.author) ? `${name(thread.last.author)} replied` : 'Quiet'
}

export function threadIconState(thread, options = {}) {
  const groups = threadActivity(thread, options)
  return { kind: groups.asking.length ? 'asking' : groups.working.length ? 'working' : 'rest',
    persona: groups.working.length === 1 ? groups.working[0].persona : null }
}

export function listeningPulse(state, now, reduced, strength = 1) {
  if (reduced || strength <= .04 || now - (state.lastPulse ?? -Infinity) < 280) return false
  state.lastPulse = now; state.pulse = (state.pulse ?? 0) + 1
  state.pulseStrength = Math.min(1, Math.max(.15, strength))
  return true
}

export function mentionedAgents(text, agents) {
  const ids = new Map(agents.map((a) => [a.id.toLowerCase(), a.id]))
  const tags = [...String(text).matchAll(/(?<![\w@])@([\p{L}\p{N}_-]+)/gu)].map((m) => m[1].toLowerCase())
  return { ids: [...new Set(tags.map((id) => ids.get(id)).filter(Boolean))], all: tags.includes('all') || tags.includes('everyone') }
}

// This previews the runtime's recipients; sending still sends the draft once.
export function listeningRecipients(thread, text, dictating, agents) {
  if (!String(text).trim() && !dictating) return []
  const mentions = mentionedAgents(text, agents)
  const ids = mentions.all ? [...mentions.ids, ...thread.members.map((m) => m.persona), thread.default]
    : mentions.ids.length ? mentions.ids : [thread.default]
  return [...new Set(ids.filter(Boolean))]
}

// The shared avatar has a text-free decorative form, so caret, copy, markdown
// and the sent payload all continue to contain exactly @persona.
export function paintMention(node, appearance) {
  const paint = appearance && avatarLook(appearance)
  const signature = JSON.stringify(paint)
  if (node.__mentionPaint === signature) return
  node.__mentionPaint = signature
  for (const child of [...node.childNodes]) if (child.nodeType === 1 && child.className?.split(' ').includes('blob')) node.removeChild(child)
  if (!appearance) {
    if (node.hasAttribute('data-face')) node.removeAttribute('data-face')
    return
  }
  node.setAttribute('data-face', appearance.initial)
  node.appendChild(blob(appearance, { size: 14, font: 8, inline: true, decorative: true }))
}
