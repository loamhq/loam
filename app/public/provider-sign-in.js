// One card and one flow for every provider. Routes and flow kinds belong to
// the catalog; this module knows no provider IDs or CLI instructions.
import { h, kept } from './ui.js'
import { providerMark } from './marks.js'
import { providerGlyph } from './sessions.js'
import { loginView } from './providers.js'

export function signInRoute(route) {
  if (typeof route !== 'string' || !/^\/v1\/providers\/[^/?#]+\/(?:login(?:\/code)?|logout)$/.test(route)) return null
  return route.slice(3)
}

function webURL(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}

export function createProviderSignIn({ api, changed, update, refresh, copy, openTab = () => window.open('about:blank', '_blank') }) {
  let active = null
  const field = h('input', { id: 'login-code', placeholder: 'Code or landing-page address',
    'aria-label': 'Paste the code you were given', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' })
  const current = (run) => active === run
  const request = (method, route, body) => {
    const path = signInRoute(route)
    if (!path) return Promise.reject(new Error('This sign-in is unavailable.'))
    return api(method, path, body)
  }
  function apply(run, answer) {
    if (!current(run)) return
    run.flow = answer
    run.error = ''
    if (answer.login) update(run.provider, answer.login, answer.state === 'done')
    if (answer.state === 'done') refresh()
    changed()
  }
  async function start(provider) {
    if (active?.busy) return
    const descriptor = provider.sign_in
    const run = { provider: provider.id, descriptor, busy: true, flow: null, error: '' }
    active = run
    field.value = ''
    // Reserve the tab during the tap, before awaiting the broker. Mobile
    // browsers otherwise block it after the asynchronous POST completes.
    let tab
    try { tab = openTab(); if (tab) tab.opener = null } catch { /* the card retains a normal link */ }
    run.tab = tab
    changed()
    try {
      const answer = await request('POST', descriptor.start, {})
      if (!current(run)) {
        tab?.close()
        if (active?.provider !== run.provider) request('DELETE', descriptor.cancel).catch(() => {})
        return
      }
      run.busy = false
      apply(run, answer)
      const url = webURL(answer.url)
      if (url && answer.state === 'pending') {
        try { tab?.location.replace(url) } catch { tab?.close() }
      } else tab?.close()
      run.tab = null
    } catch (error) {
      tab?.close()
      if (!current(run)) return
      run.tab = null
      run.busy = false
      run.error = error.message
      changed()
    }
  }
  async function check({ quiet = false } = {}) {
    const run = active
    if (!run || run.busy || run.polling || run.flow?.state !== 'pending') return
    const revision = run.revision
    run.polling = true
    try {
      const answer = await request('GET', run.descriptor.status)
      if (current(run) && !run.busy && run.revision === revision) apply(run, answer)
    } catch (error) {
      if (!quiet && current(run)) { run.error = error.message; changed() }
    } finally { run.polling = false }
  }
  async function submit(event) {
    event?.preventDefault()
    const run = active
    if (!run || run.busy || run.flow?.accepts_code !== true) return
    const code = field.value.trim()
    if (!code) { run.error = 'Paste the code you were given.'; changed(); return }
    run.busy = true
    run.revision = (run.revision ?? 0) + 1
    run.error = ''
    changed()
    try {
      const answer = await request('POST', run.descriptor.code, { code })
      if (!current(run)) return
      field.value = ''
      run.busy = false
      apply(run, answer)
    } catch (error) {
      if (!current(run)) return
      run.busy = false
      run.error = error.message
      // Nothing is waiting after a timeout. Retrying starts a fresh flow.
      if ([409, 405].includes(error.status)) run.flow = { ...run.flow, state: 'failed', accepts_code: false }
      changed()
    }
  }
  function cancel() {
    const run = active
    active = null
    field.value = ''
    run?.tab?.close()
    if (run && !run.signingOut && (run.busy || run.flow?.state === 'pending')) request('DELETE', run.descriptor.cancel).catch(() => {})
    changed()
  }
  async function signOut(provider) {
    if (active?.busy) return
    const run = { provider: provider.id, descriptor: provider.sign_in, busy: true, error: '', signingOut: true }
    active = run
    changed()
    try {
      const answer = await request('POST', run.descriptor.sign_out, {})
      if (!current(run)) { refresh(); return }
      if (answer.login) update(run.provider, answer.login, false)
      active = null
      refresh()
      changed()
    } catch (error) {
      if (!current(run)) return
      run.busy = false
      run.error = error.message
      changed()
    }
  }
  function copyButton(value) {
    return h('button', { class: 'pillbtn content-mark devcopy', type: 'button', text: active?.copied === value ? 'Copied' : 'Copy', onclick: async () => {
      const run = active
      if (await copy(value) && current(run)) { run.copied = value; changed() }
    } })
  }
  function card(provider, { heading = true, onboarding = false } = {}) {
    const descriptor = provider?.sign_in
    if (!descriptor) return null
    const run = active?.provider === provider.id ? active : null
    const flow = run?.flow
    const pending = flow?.state === 'pending'
    const error = run?.error || (flow?.state === 'failed' ? flow.message || 'The sign-in failed. Try again.' : '')
    const signedIn = Boolean(provider.connected)
    const device = descriptor.kind === 'device-code'
    const url = webURL(flow?.url)
    const view = loginView(provider)
    const button = (text, onclick, primary = false) => h('button', { class: primary ? 'primary' : 'quietlink content-mark', type: 'button', text,
      disabled: Boolean(run?.busy), onclick })
    const startButton = () => button(run?.busy ? 'Starting…' : error || flow?.state === 'idle' ? 'Try again' : descriptor.label || 'Sign in', () => start(provider), true)
    return h('section', { class: 'card provider-sign-in', key: `sign-in:${provider.id}`, 'aria-label': `${provider.name} sign-in` },
      heading ? h('div', { class: 'sign-in-heading' },
        h('span', { class: `icon-tile glyph ${provider.id}` }, providerMark(provider.id, 24, providerGlyph(provider.id))),
        h('div', { class: 'setrow-title', text: provider.name })) : null,
      h('div', { class: 'signline', 'aria-live': 'polite', text: signedIn
        ? provider.login?.account ? `Signed in as ${provider.login.account}` : 'Signed in'
        : 'Not signed in' }),
      signedIn && view?.expiresAt ? h('div', { class: `note signline ${view.level}`, text: view.line }) : null,
      pending || (run?.busy && !run.signingOut) ? h('form', { class: `loginflow${device ? ' device' : ''}`, onsubmit: submit },
        url ? h('div', { class: 'sign-in-links' },
          h('a', { class: 'loginlink', href: url, target: '_blank', rel: 'noopener noreferrer', text: 'Open sign-in page' }), copyButton(url)) : null,
        pending && device && flow.code ? h('div', { class: 'devrow' },
          h('div', { class: 'kicker', text: 'Enter this code on the sign-in page' }),
          h('div', { class: 'devcode', text: flow.code }), copyButton(flow.code)) : null,
        pending && !device && flow.accepts_code === true ? h('div', { class: 'field flat' },
          h('label', { for: 'login-code', text: 'Paste the code you were given' }), kept(field)) : null,
        h('div', { class: 'loginbtns' },
          h('button', { class: 'primary', type: device ? 'button' : 'submit',
            disabled: Boolean(run.busy) || (!device && flow?.accepts_code !== true),
            text: run.busy ? flow ? 'Sending…' : 'Starting…' : 'Done', onclick: device ? () => check() : null }),
          h('button', { class: 'cancel content-mark', type: 'button', text: 'Cancel', onclick: cancel })))
        : h('div', { class: `loginbtns${signedIn ? ' sign-in-actions' : ''}` }, signedIn
          ? onboarding ? h('span', { class: 'welcome-connected', text: '✓ Connected' }) : [button('Sign in again', () => start(provider)), descriptor.sign_out ? button(run?.busy ? 'Signing out…' : 'Sign out', () => signOut(provider)) : null]
          : startButton()),
      error ? h('div', { class: 'reason', role: 'alert', text: error }) : null)
  }
  return { card, cancel, poll: () => check({ quiet: true }) }
}
