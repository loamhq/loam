// A portal expiry is terminal for this page. Every API caller shares the latch,
// including background speech lookups and stream reconnects.
//
// One refused request is not an expiry (0.2.1, found by the 0.2.0 walk): the
// login renews its session when a person proves who they are again, as adding
// a passkey asks, and every request between the old cookie's end and the new
// one's arrival comes back anonymous while the session is fine. Measured on a
// fresh install (0.2.1): the one-time code's `PUT /api/user/session/elevation`
// ends the old session as it starts and sends the new cookie with its answer
// about 0.9 s later; 21 polls in that gap were anonymous. Before the latch
// closes, the page asks again, backing off: it fetches `PROBE` (behind the
// same login, answered by the app at once) after each wait in
// `timing.confirmSchedule`, until one is let through. Requests refused at the
// same moment share one confirmation. Signed in, the refused request is sent
// again once (the login refused it before the app saw it, so nothing ran
// twice); only a refusal of that too, or of every probe, ends the session.
let expired = false
let confirming = null
export const PROBE = '/up'
export const timing = { confirmSchedule: [700, 1500, 3000, 6000] }
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
const refused = () => Object.assign(new Error('Sign in again'), { status: 401 })
const send = (input, options) => globalThis.fetch(input, { ...options, redirect: 'manual' })
function stillSignedIn() {
  confirming ??= (async () => {
    try {
      for (const wait of timing.confirmSchedule) {
        await new Promise(resolve => setTimeout(resolve, wait))
        try {
          if (!authFailure(await send(PROBE, { cache: 'no-store', credentials: 'same-origin' }))) return true
        } catch { /* Offline is not signed out: the next probe asks again. */ }
      }
      return false
    } finally {
      confirming = null
    }
  })()
  return confirming
}
const resendable = options => {
  const body = options?.body
  return !(typeof ReadableStream === 'function' && body instanceof ReadableStream)
}
export async function authenticatedFetch(input, options = {}) {
  if (expired) throw refused()
  const response = await send(input, options)
  if (!authFailure(response)) return response
  if (resendable(options) && await stillSignedIn() && !expired) {
    const again = await send(input, options)
    if (!authFailure(again)) return again
  }
  expireAuth()
  throw refused()
}
export function failureSentence(text, status = null) {
  if (!/\/v1\/|\bDELETE\b/.test(String(text))) return text
  if (status === 404) return 'This file or folder isn’t there.'
  return 'Couldn’t start — the box is busy; try again in a moment.'
}
