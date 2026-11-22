// Permanent face identity slots. Installation-supplied AVATAR_VARIANTS win
// where set; otherwise each agent keeps the slot stored on its record so faces
// stay distinct until a theme's set runs out and never move when other agents
// are added, renamed or deleted. Unknown IDs fall back to the FNV hash.
export function readFaceVariants(value = {}) {
  const input = typeof value === 'string' ? JSON.parse(value || '{}') : value
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('AVATAR_VARIANTS must be a JSON object of agent IDs to variant slots.')
  const result = {}, used = new Set()
  for (const [id, slot] of Object.entries(input)) {
    if (!id || !Number.isInteger(slot) || slot < 0 || slot > 7 || used.has(slot)) throw new Error('AVATAR_VARIANTS needs unique integer slots from 0 to 7.')
    used.add(slot)
    Object.defineProperty(result, id, { value:slot, enumerable:true })
  }
  return result
}
export function readFaceSlots(value = {}) {
  const result = Object.create(null)
  if (Array.isArray(value)) {
    for (const entry of value) {
      const id = entry?.id
      const slot = entry?.face_slot ?? entry?.faceSlot
      if (typeof id === 'string' && id && Number.isInteger(slot) && slot >= 0) result[id] = slot
    }
    return result
  }
  if (!value || typeof value !== 'object') return result
  for (const [id, slot] of Object.entries(value)) {
    if (id && Number.isInteger(slot) && slot >= 0) result[id] = slot
  }
  return result
}
function fnvHash(id) {
  let hash = 2166136261
  for (const char of String(id)) hash = Math.imul(hash ^ char.codePointAt(0), 16777619) >>> 0
  return hash
}
let fixed = readFaceVariants(globalThis.LOAM_AVATAR_VARIANTS ?? {})
let assigned = readFaceSlots(globalThis.LOAM_FACE_SLOTS ?? {})
export function setFaceVariants(value = {}) { fixed = readFaceVariants(value) }
export function setFaceSlots(value = {}) { assigned = readFaceSlots(value) }
export function recordFaceSlots(value = {}) {
  const next = readFaceSlots(value)
  for (const [id, slot] of Object.entries(next)) assigned[id] = slot
}
export function variantFor(id, countOrOptions = 8, slot = null) {
  let count = 8
  let explicit = null
  // Guard against Array.prototype.map(variantFor), which passes (id, index, array).
  if (Array.isArray(slot)) {
    count = 8
    explicit = null
  } else if (countOrOptions && typeof countOrOptions === 'object') {
    count = Number.isInteger(countOrOptions.count) && countOrOptions.count > 0 ? countOrOptions.count : 8
    explicit = Number.isInteger(countOrOptions.slot) && countOrOptions.slot >= 0 ? countOrOptions.slot : null
  } else {
    count = Number.isInteger(countOrOptions) && countOrOptions > 0 ? countOrOptions : 8
    explicit = Number.isInteger(slot) && slot >= 0 ? slot : null
  }
  const key = String(id ?? '')
  if (Object.hasOwn(fixed, key)) return fixed[key] % count
  if (explicit !== null) return explicit % count
  if (Object.hasOwn(assigned, key)) return assigned[key] % count
  return fnvHash(key) % count
}
