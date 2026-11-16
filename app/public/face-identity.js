// Permanent, installation-supplied identity slots. Append new IDs without
// renumbering existing entries. Unknown IDs keep the original eight-way hash.
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
let fixed = readFaceVariants(globalThis.LOAM_AVATAR_VARIANTS ?? {})
export function setFaceVariants(value = {}) { fixed = readFaceVariants(value) }
export function variantFor(id) {
  if (Object.hasOwn(fixed, id)) return fixed[id]
  let hash = 2166136261
  for (const char of String(id)) hash = Math.imul(hash ^ char.codePointAt(0), 16777619) >>> 0
  return hash % 8
}
