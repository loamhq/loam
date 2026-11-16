import test from 'node:test'
import assert from 'node:assert/strict'
import { formatTime, formatDate } from '../public/time.js'
import { createAgents, agentOf, agentName, agentLook, providerGlyph, when } from '../public/sessions.js'
const NOW = Date.parse('2026-09-18T12:00:00Z')
const AGENTS = [
  { id: 'alfred', name: 'Alfred', emoji: '🧰' },
  { id: 'sable', name: 'Sable', emoji: null },
  { id: 'scout', name: 'Scout', emoji: '🔭' },
]

test('an agent is the same colour and the same shape on every device', () => {
  assert.deepEqual(agentLook('alfred'), agentLook('alfred'))
  assert.notDeepEqual(agentLook('alfred').color, agentLook('beatrice').color)
  assert.equal(agentLook('alfred').initial, 'A')
  assert.equal(agentLook('').initial, '?')
})

test('a provider this page has never met still gets a badge', () => {
  assert.equal(providerGlyph('claude-code'), 'C')
  assert.equal(providerGlyph('codex'), 'X')
  assert.equal(providerGlyph('something-new'), 'S')
})

test('the time column is a clock today, a weekday this week, a date beyond it', () => {
  const anHourAgo = new Date(NOW - 3600000)
  const threeDays = new Date(NOW - 3 * 86400000)
  const aMonth = new Date(NOW - 30 * 86400000)

  assert.equal(when(new Date(NOW - 5000).toISOString(), NOW), 'Now')
  assert.equal(
    when(anHourAgo.toISOString(), NOW),
    formatTime(anHourAgo),
  )
  assert.equal(when(new Date(NOW - 86400000).toISOString(), NOW), 'Yesterday')
  assert.equal(when(threeDays.toISOString(), NOW), formatDate(threeDays, { weekday: 'short' }))
  assert.equal(when(aMonth.toISOString(), NOW), formatDate(aMonth, { day: 'numeric', month: 'short' }))
  assert.equal(when(null, NOW), '', 'a session that has never spoken says nothing')
})

test('a name the host states is the name shown, and the id is what starts a session', () => {
  const agents = createAgents(AGENTS)
  assert.equal(agentName(agents, 'scout'), 'Scout')
  assert.equal(agentOf(agents, 'scout').id, 'scout')
  assert.equal(agentOf(agents, 'scout').emoji, '🔭')
})

test('one letter for an avatar, two when two agents share the first', () => {
  const agents = createAgents(AGENTS)
  assert.equal(agentLook('alfred', agents).initial, 'A')
  assert.equal(agentLook('scout', agents).initial, 'Sc')
  assert.equal(agentLook('sable', agents).initial, 'Sa')
})

test('an agent alone with its letter keeps the single one', () => {
  const agents = createAgents([{ id: 'scout', name: 'Scout', emoji: null }])
  assert.equal(agentLook('scout', agents).initial, 'S')
})

test('a name the host did not give falls back to the capitalised id, never to nothing', () => {
  const agents = createAgents([{ id: 'quill' }, { id: 'nomad', name: '   ' }])
  assert.equal(agentName(agents, 'quill'), 'Quill')
  assert.equal(agentName(agents, 'nomad'), 'Nomad')
  // An agent on a record that the listing no longer has is still a row.
  assert.equal(agentName(agents, 'gone-away'), 'Gone-away')
  assert.equal(agentLook('gone-away', agents).initial, 'G')
})

test('the emoji is the agent’s own and is never the avatar', () => {
  const agents = createAgents(AGENTS)
  // The blob is the page's: its colour and its shape are computed from the id,
  // and a null emoji is not a gap to fill with a guess.
  assert.equal(agentLook('sable', agents).emoji, null)
  assert.equal(agentLook('scout', agents).initial, 'Sc')
  assert.match(agentLook('scout', agents).color, /^var\(--persona-[a-z]+\)$/)
})

test('a rename on the host does not move an agent’s colour, because the id did not', () => {
  const before = agentLook('scout', createAgents([{ id: 'scout', name: 'Scout' }]))
  const after = agentLook('scout', createAgents([{ id: 'scout', name: 'Scout the Second' }]))
  assert.equal(after.color, before.color)
  assert.equal(after.shape, before.shape)
  assert.equal(after.name, 'Scout the Second')
})

test('the answer being missing or the wrong shape is no agents, not a crash', () => {
  assert.deepEqual(createAgents(undefined).list, [])
  assert.deepEqual(createAgents(['alfred']).list, []) // the shape before 2026-09-18
  assert.equal(createAgents([{ id: '' }, null]).list.length, 0)
})

test('roster order, additions and display names preserve the original ID colours', () => {
  const entries = Array.from({ length: 8 }, (_, i) => ({ id: `persona-${i}`, name: `Persona ${i}` }))
  for (const roster of [entries, [...entries].reverse(), [...entries, { id: 'new-persona', name: 'New' }]]) {
    const agents = createAgents(roster)
    for (const a of entries) assert.equal(agentLook(a.id, agents).color, agentLook(a.id).color)
  }
})
