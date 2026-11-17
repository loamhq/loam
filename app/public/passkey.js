import { h } from './ui.js'
import { authenticatedFetch } from './auth.js'
import { copyText } from './clipboard.js'

export function authSettingsURL(address = location.href) {
  const url = new URL(address)
  url.protocol = 'https:'; url.hostname = `auth.${url.hostname}`; url.port = ''; url.pathname = '/settings'; url.search = ''; url.hash = ''
  return url.href
}
export function createPasskey({ changed, request = authenticatedFetch, later = setTimeout, cancel = clearTimeout, now = Date.now }) {
  const st = { active: false, code: null, error: '', checking: false, copied: false }
  let timer, revision = 0
  function stop() {
    revision++; cancel(timer); st.active = false; st.code = null; st.error = ''; st.checking = false; st.copied = false
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
  function view() {
    const valid = st.code && now() - st.at < 900000
    const age = valid ? Math.max(st.age, Math.floor((now() - st.at) / 1000)) : null
    return h('section', { class: 'card passkey-card' },
      h('h3', { text: 'A quicker way in' }),
      h('p', { text: 'Add a passkey to sign in with your face, fingerprint or device screen lock.' }),
      h('a', { class: 'pillbtn primary', href: authSettingsURL(), target: '_blank', rel: 'noopener noreferrer', text: st.active ? 'Open sign-in settings again' : 'Add a passkey', onclick: start }),
      st.active ? h('div', { class: 'passkey-flow' },
        h('p', { text: 'In the page that opens, choose Add a passkey.' }),
        h('p', { class: 'note', text: 'When it asks for a one-time code, come back here. Keep this page open until you’ve added your passkey.' }),
        h('div', { class: 'passkey-code-area', role: 'status', 'aria-live': 'polite' },
          h('p', { text: valid ? 'Loam’s sign-in sent you this code' : 'Waiting for your sign-in code…' }),
          valid ? [h('div', { class: 'passkey-code', text: st.code }), h('p', { class: 'note', text: age < 60 ? `${age} seconds ago` : `${Math.floor(age / 60)} minutes ago` }),
            h('button', { class: 'pillbtn', text: st.copied ? 'Copied' : 'Copy code', onclick: async () => { const value = st.code; if (await copyText(value) && value === st.code) { st.copied = true; changed() } } })] : null),
        h('button', { class: 'pillbtn', text: 'Done', onclick: () => { stop(); changed() } })) : null,
      st.error ? h('p', { class: 'reason', role: 'status', text: st.error }) : null)
  }
  return { st, start, stop, poll, view }
}
