// Shared agent identity, turn token labels and locale-aware list timestamps.
// The retired session-list model has no place in the thread UI.
import { formatTime, formatDate } from './time.js'

import { AGENT_COLOURS, AGENT_TINTS, readAvatarColours } from './avatar-colours.js'

export { AGENT_COLOURS, AGENT_TINTS } from './avatar-colours.js'

let avatarColours = {}

export function setAvatarColours(value = {}) { avatarColours = readAvatarColours(value) }

export const AGENT_SHAPES = [
  '72% 28% 72% 28% / 28% 72% 28% 72%',
  '999px',
  '34%',
  '50% 50% 50% 50% / 64% 64% 36% 36%',
  '58% 42% 55% 45% / 48% 56% 44% 52%',
  '40% 60% 60% 40% / 68% 32% 68% 32%',
]

const PROVIDER_GLYPHS = { 'claude-code': 'C', codex: 'X', antigravity: 'A', grok: 'G', opencode: 'O' }

function agentDisplayName(agent) {
  return [agent.display, agent.name].find(value => typeof value === 'string' && value.trim())?.trim() ?? capitalise(agent.id)
}

export function createAgents(entries = []) {
  const clean = (Array.isArray(entries) ? entries : [])
    .filter((entry) => entry && typeof entry === 'object' && typeof entry.id === 'string' && entry.id !== '')
    .map((entry) => {
      const slot = entry.face_slot ?? entry.faceSlot
      return {
        id: entry.id,
        name: agentDisplayName(entry),
        ...(typeof entry.role === 'string' && entry.role.trim() ? { role: entry.role.trim() } : {}),
        ...(typeof entry.description === 'string' && entry.description.trim() ? { description: entry.description.trim() } : {}),
        face: entry.face ?? null,
        ...(Number.isInteger(slot) && slot >= 0 ? { faceSlot: slot } : {}),
        archived: entry.archived ?? null,
      }
    })

  const shared = new Map()
  for (const agent of clean) {
    const first = letters(agent.name)[0] ?? ''
    shared.set(first, (shared.get(first) ?? 0) + 1)
  }
  const byId = new Map()
  for (const agent of clean) {
    const chars = letters(agent.name)
    const first = chars[0] ?? ''
    byId.set(agent.id, {
      ...agent,
      initial: shared.get(first) > 1 && chars[1] ? `${first}${chars[1].toLowerCase()}` : (first || '?'),
    })
  }
  return { list: clean.map((a) => byId.get(a.id)), byId }
}

export function agentOf(agents, id) {
  const key = String(id ?? '')
  const known = agents?.byId?.get(key)
  if (known) return known
  return { id: key, name: capitalise(key), initial: letters(key)[0] ?? '?' }
}

export function agentName(agents, id) {
  return agentOf(agents, id).name
}

function letters(name) {
  const clean = String(name ?? '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim()
  if (clean === '') return []
  return [clean[0].toUpperCase(), ...clean.slice(1)]
}

function capitalise(id) {
  const clean = String(id ?? '')
  return clean === '' ? '' : clean[0].toUpperCase() + clean.slice(1)
}

export function agentLook(id, agents = null) {
  const key = String(id ?? '')
  let hash = 0
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0
  const agent = agentOf(agents, key)
  const chosen = AGENT_COLOURS.indexOf(avatarColours[key])
  const colour = chosen < 0 ? hash % AGENT_COLOURS.length : chosen
  const position = agents?.list?.findIndex(a => a.id === key) ?? -1
  return {
    agentId: key,
    configuredColour: chosen >= 0,
    // Roster order is stable across surfaces. Spread neighbours over the ramp,
    // including both endpoints within the first seven, before reusing a tone.
    tone: [0, 6, 2, 8, 4, 9, 1, 7, 3, 5][(position < 0 ? hash : position) % 10],
    color: `var(--${AGENT_COLOURS[colour]})`,
    ink: `var(--${AGENT_COLOURS[colour]}-ink, var(--text-on-avatar))`,
    tint: `var(--${AGENT_TINTS[colour]})`,
    shape: AGENT_SHAPES[hash % AGENT_SHAPES.length],
    initial: agent.initial || '?',
    name: agent.name,
    imageSrc: agent.face?.url?.replace(/^\/v1\//, '/api/') || null,
    ...(Number.isInteger(agent.faceSlot) && agent.faceSlot >= 0 ? { faceSlot: agent.faceSlot } : {}),
  }
}

function initialOf(name) {
  return letters(name)[0] ?? '?'
}

export function providerGlyph(id) {
  return PROVIDER_GLYPHS[id] ?? initialOf(String(id ?? '?'))
}

export const SETTLED_STATUSES = new Set(['needs_input', 'done', 'failed', 'idle'])

export function tokenWords(n) {
  if (n >= 1e6) return `${trimNumber(n / 1e6)}M`
  if (n >= 1000) return `${trimNumber(n / 1000)}k`
  return String(n)
}

function trimNumber(n) {
  const rounded = n >= 100 ? Math.round(n) : Number(n.toFixed(1))
  return String(rounded)
}

export function longAgo(ms) {
  const minutes = Math.floor(Math.max(0, ms) / 60000)
  if (minutes < 1) return 'under a minute'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return hours === 1 ? 'an hour' : `${hours} hours`
  const days = Math.round(hours / 24)
  return days === 1 ? 'a day' : `${days} days`
}

export function when(iso, now = Date.now()) {
  const t = timeOf(iso)
  if (t === 0) return ''
  const age = now - t
  if (age < 60000) return 'Now'
  const then = new Date(t)
  const today = new Date(now)
  const sameDay = then.toDateString() === today.toDateString()
  if (sameDay) return formatTime(then)
  const yesterday = new Date(now - 86400000)
  if (then.toDateString() === yesterday.toDateString()) return 'Yesterday'
  if (age < 7 * 86400000) return formatDate(then, { weekday: 'short' })
  return formatDate(then, { day: 'numeric', month: 'short' })
}

function timeOf(iso) {
  const time = Date.parse(iso ?? '')
  return Number.isNaN(time) ? 0 : time
}
