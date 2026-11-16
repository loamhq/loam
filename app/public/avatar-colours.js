// Original eleven-slot palette, in legacy order. Names and positions are stable:
// persisted exceptions and the ID hash both depend on them, never roster order.
export const AGENT_COLOURS = ['persona-slate', 'persona-olive', 'persona-rust', 'persona-plum', 'persona-teal', 'persona-rose', 'persona-gold', 'persona-blue', 'persona-taupe', 'persona-sage', 'persona-mauve']
export const AGENT_TINTS = AGENT_COLOURS.map(token => `${token}-tint`)
// Accept both historical slots and the Newsprint-era input names. Palette
// keeps the original eleven names/order; aliases never create extra CSS hues.
export const LEGACY_AGENT_COLOURS = {
  'persona-ink-blue': 'persona-blue', 'persona-forest': 'persona-sage',
  'persona-ochre': 'persona-gold', 'persona-oxblood': 'persona-rose',
  'persona-rosewood': 'persona-mauve', 'persona-graphite': 'persona-taupe',
}
// These are input aliases, never names used to construct a CSS reference.
const legacySlots = [49, 51, 52, 54, 56, 58, 60, 62, 64, 66, 68]
export function readAvatarColours(value = {}) {
  const input = typeof value === 'string' ? JSON.parse(value || '{}') : value
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('AVATAR_COLOURS must be a JSON object of agent IDs to palette tokens.')
  const colours = {}
  for (const [id, supplied] of Object.entries(input)) {
    const legacy = /^c(\d+)$/.exec(supplied)
    const named = legacy ? AGENT_COLOURS[legacySlots.indexOf(Number(legacy[1]))] : supplied
    const token = Object.hasOwn(LEGACY_AGENT_COLOURS, named) ? LEGACY_AGENT_COLOURS[named] : named
    if (!id || !AGENT_COLOURS.includes(token)) throw new Error('AVATAR_COLOURS uses an unknown palette token.')
    Object.defineProperty(colours, id, { value: token, enumerable: true, configurable: true, writable: true })
  }
  return colours
}
