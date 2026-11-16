// A portal expiry is terminal for this page. Every API caller shares the latch,
// including background speech lookups and stream reconnects.
let expired = false
export const authExpired = () => expired
export function authFailure(response) {
  const type = response?.headers?.get?.('content-type') ?? ''
  return response?.status === 401 || response?.status === 303 || response?.type === 'opaqueredirect'
    || (response?.redirected && /text\/html/i.test(type))
}
export function expireAuth() {
  if (expired) return
  expired = true
  if (typeof globalThis.dispatchEvent === 'function' && typeof CustomEvent === 'function') globalThis.dispatchEvent(new CustomEvent('loam:signin'))
}
export async function authenticatedFetch(input, options = {}) {
  if (expired) throw Object.assign(new Error('Sign in again'), { status: 401 })
  const response = await globalThis.fetch(input, { ...options, redirect: 'manual' })
  if (authFailure(response)) {
    expireAuth()
    throw Object.assign(new Error('Sign in again'), { status: 401 })
  }
  return response
}
export function failureSentence(text) {
  return /\/v1\/|\bDELETE\b/.test(String(text)) ? 'Couldn’t start — the box is busy; try again in a moment.' : text
}
