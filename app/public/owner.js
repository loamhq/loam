// Identity comes only from GET /owner. Unknown authors are never assumed to be
// the owner. An optimistic message is marked locally until acceptance.
let current = { id: null, name: 'You', accepts: [] }
export function setOwner(value) {
  if (typeof value?.id !== 'string' || !value.id || typeof value.name !== 'string' || !value.name.trim()
    || !Array.isArray(value.accepts) || !value.accepts.every(id => typeof id === 'string')) throw new Error('The runtime returned an unreadable owner.')
  current = { id: value.id, name: value.name, accepts: [...value.accepts] }
}
export const owner = () => current
export const isOwner = id => current.accepts.includes(id)
export const ownerInitial = () => Array.from(current.name.trim())[0]?.toLocaleUpperCase() || '?'
