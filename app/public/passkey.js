import { h } from './ui.js'
import { authenticatedFetch } from './auth.js'
import { copyText } from './clipboard.js'
import { formatDate } from './time.js'
import { installation } from './installation.js'

// The login portal is `auth.` in front of the install's domain
// (deploy/login/README.md): its `/settings` lists, adds and removes passkeys
// (asking the person to confirm who they are first), and its `/logout` signs
// this browser out and says so. The domain, not the app's own host: the app
// is at the apex, but staging is `next.<domain>`, whose portal is still
// `auth.<domain>` (`auth.next.<domain>` has no record and no certificate).
// A page whose host is not under the install's domain (the design preview)
// puts `auth.` in front of its own host, as it always did.
function portal(address, pathname, domain = installation().appsDomain) {
  const url = new URL(address)
  const host = url.hostname
  const under = domain && (host === domain || host.endsWith(`.${domain}`))
  url.protocol = 'https:'
  url.hostname = under ? `auth.${domain}` : host.startsWith('auth.') ? host : `auth.${host}`
  url.port = ''; url.pathname = pathname; url.search = ''; url.hash = ''
  return url.href
}
export const authSettingsURL = (address = location.href, domain) => portal(address, '/settings', domain)
export const authLogoutURL = (address = location.href, domain) => portal(address, '/logout', domain)

// The passkeys themselves are the portal's (Authelia's
// `/api/secondfactor/webauthn/credentials`, on its own origin, which the page
// cannot read). The app shows them when its host relays that list at
// `/_loam/passkeys`, beside `/_loam/login-code`:
// `{passkeys: [{id, description, created_at, last_used_at}]}`. Without the
// relay (404) the card links to the portal's list instead. Removing one is
// always the portal's, where the person confirms who they are first.
const passkeyDate = value => value && Number.isFinite(Date.parse(value)) && new Date(value).getFullYear() >= 2000 ? formatDate(value) : null

export function createPasskey({ changed, request = authenticatedFetch, later = setTimeout, cancel = clearTimeout, now = Date.now }) {
  const st = { active: false, code: null, error: '', checking: false, copied: false, passkeys: null, listed: false, listing: false }
  let timer, revision = 0, listing = 0
  function stop() {
    revision++; cancel(timer); st.active = false; st.code = null; st.error = ''; st.checking = false; st.copied = false
  }
  async function loadPasskeys() {
    const ticket = ++listing
    let list = null
    st.listing = true
    try {
      const res = await request('/_loam/passkeys', { cache: 'no-store', credentials: 'same-origin' })
      const body = res.ok ? await res.json().catch(() => null) : null
      if (Array.isArray(body?.passkeys)) list = body.passkeys.filter(p => p && p.id != null).map(p => ({ id: String(p.id), name: String(p.description || p.name || 'Passkey'), created_at: p.created_at ?? null, last_used_at: p.last_used_at ?? null }))
    } catch { /* No relay, or it did not answer: the portal's own list stands in. */ }
    if (ticket !== listing) return
    st.passkeys = list; st.listed = true; st.listing = false
    changed()
  }
  async function poll() {
    if (!st.active || st.checking) return
    const ticket = revision
    st.checking = true
    try {
      const res = await request('/_loam/login-code', { cache: 'no-store', credentials: 'same-origin' })
      const body = await res.json()
      if (!res.ok) throw Object.assign(new Error(body.error || 'The sign-in code couldn’t be read.'), { status: res.status })
      if (!st.active || ticket !== revision) return
      // Never persist, log, or hand this value to the broker. Its age is also
      // checked locally so a suspended tab cannot keep displaying an old code.
      const at = Date.parse(body.at)
      const valid = typeof body.code === 'string' && Number.isFinite(body.age_seconds) && body.age_seconds < 900 && Number.isFinite(at) && now() - at < 900000
      const code = valid ? body.code : null
      if (code !== st.code) st.copied = false
      st.code = code; st.at = valid ? at : null; st.age = body.age_seconds; st.error = ''
    } catch (e) {
      if (!st.active || ticket !== revision) return
      st.code = null; st.error = e.status === 403 ? 'Only the owner of this Loam can see sign-in codes.' : e.status ? e.message : 'The sign-in code couldn’t be read. We’ll try again in a moment.'
      if (e.status === 403 || e.status === 401) st.active = false
    } finally {
      if (ticket === revision) { st.checking = false; changed(); if (st.active) timer = later(poll, 3000) }
    }
  }
  function start() { stop(); st.active = true; changed(); poll() }
  function passkeyList() {
    if (!st.passkeys?.length) return null
    return h('ul', { class: 'passkey-list', 'aria-label': 'Your passkeys' }, ...st.passkeys.map(p => {
      const added = passkeyDate(p.created_at), used = passkeyDate(p.last_used_at)
      return h('li', { class: 'passkey-item', key: p.id },
        h('div', { class: 'passkey-item-who' },
          h('div', { class: 'passkey-item-name', text: p.name }),
          h('div', { class: 'note', text: [added ? `Added ${added}` : null, used ? `last used ${used}` : 'not used yet'].filter(Boolean).join(' · ') })),
        h('a', { class: 'pillbtn passkey-remove', href: authSettingsURL(), target: '_blank', rel: 'noopener noreferrer', 'aria-label': `Remove ${p.name}`, text: 'Remove' }))
    }))
  }
  function passwordCard() {
    return h('section', { class: 'card passkey-card password-card' },
      h('h3', { text: 'Password' }),
      h('p', { text: 'Change the password you sign in with.' }),
      h('div', { class: 'passkey-actions' },
        h('a', { class: 'pillbtn', href: authSettingsURL(), target: '_blank', rel: 'noopener noreferrer', text: 'Change password' })))
  }
  function signOutCard() {
    return h('section', { class: 'card passkey-card signout-card' },
      h('h3', { text: 'Sign out' }),
      h('p', { text: 'Sign out of Loam in this browser. Your agents keep working.' }),
      h('a', { class: 'pillbtn signout', href: authLogoutURL(), text: 'Sign out' }))
  }
  // Settings › Account (2026-11-18): the password, the passkeys, sign out.
  function view({ password = false } = {}) {
    return h('div', { class: 'passkey-sections' }, password ? passwordCard() : null, passkeysCard(), signOutCard())
  }
  function passkeysCard() {
    const valid = st.code && now() - st.at < 900000
    const age = valid ? Math.max(st.age, Math.floor((now() - st.at) / 1000)) : null
    const known = Array.isArray(st.passkeys), some = known && st.passkeys.length > 0
    return h('section', { class: 'card passkey-card' },
        h('h3', { text: some ? 'Your passkeys' : 'A quicker way in' }),
        h('p', { text: some ? 'Sign in with your face, fingerprint or device screen lock. Add one on each phone or computer you use.' : 'Add a passkey to sign in with your face, fingerprint or device screen lock.' }),
        passkeyList(),
        h('div', { class: 'passkey-actions' },
          h('a', { class: 'pillbtn primary', href: authSettingsURL(), target: '_blank', rel: 'noopener noreferrer', text: st.active ? 'Open sign-in settings again' : some ? 'Add another passkey' : 'Add a passkey', onclick: start }),
          st.listed && !known ? h('a', { class: 'quietlink passkey-manage', href: authSettingsURL(), target: '_blank', rel: 'noopener noreferrer', text: 'See or remove your passkeys' }) : null),
        st.active ? h('div', { class: 'passkey-flow' },
          h('p', { text: 'In the page that opens, choose Add a passkey.' }),
          h('p', { class: 'note', text: 'When it asks for a one-time code, come back here. Keep this page open until you’ve added your passkey.' }),
          h('div', { class: 'passkey-code-area', role: 'status', 'aria-live': 'polite' },
            h('p', { text: valid ? 'Loam’s sign-in sent you this code' : 'Waiting for your sign-in code…' }),
            valid ? [h('div', { class: 'passkey-code', text: st.code }), h('p', { class: 'note', text: age < 60 ? `${age} seconds ago` : `${Math.floor(age / 60)} minutes ago` }),
              h('button', { class: 'pillbtn', type: 'button', text: st.copied ? 'Copied' : 'Copy code', onclick: async () => { const value = st.code; if (await copyText(value) && value === st.code) { st.copied = true; changed() } } })] : null),
          h('button', { class: 'pillbtn', type: 'button', text: 'Done', onclick: () => { stop(); loadPasskeys(); changed() } })) : null,
        st.error ? h('p', { class: 'reason', role: 'status', text: st.error }) : null)
  }
  return { st, start, stop, poll, loadPasskeys, view }
}
