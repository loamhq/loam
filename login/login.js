// Loam's sign-in: the page a person meets at the login portal, in front of
// Authelia's own API (login/README.md). Caddy serves this file and the page
// for the portal's own routes; every request it makes goes to /api/ on the
// same origin, which is Authelia, unchanged. Nothing here decides who gets
// in: Authelia checks every factor, keeps the session and validates every
// redirect. This file only draws the forms and passes their answers on.
'use strict';
(() => {
  const body = document.body
  const view = document.getElementById('view')
  const HOME = body.dataset.home || ''
  const REMEMBER = body.dataset.remember === 'on'
  const RESET = body.dataset.reset === 'on'
  const PASSKEYS = body.dataset.passkeys === 'on' && !!window.PublicKeyCredential
  // A staged rollout (login.page = "staged") serves this page only to a
  // request that asks for it; links between its own pages keep asking.
  const KEEP = body.dataset.keep || ''
  const query = new URLSearchParams(location.search)
  const RD = query.get('rd') || ''
  const RM = query.get('rm') || ''
  const METHOD_KEY = 'loam.signin.method'
  const SEEN_KEY = 'loam.signin.seen'

  // ---------------------------------------------------------------------------
  // Small things
  // ---------------------------------------------------------------------------

  function h(tag, props, ...kids) {
    const el = document.createElement(tag)
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue
      if (k === 'text') el.textContent = v
      else if (k === 'class') el.className = v
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v)
      else el.setAttribute(k, v === true ? '' : v)
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid)
    return el
  }
  const show = (...nodes) => { view.replaceChildren(...nodes.flat().filter(Boolean)) }
  const remembered = (key) => { try { return localStorage.getItem(key) } catch { return null } }
  const remember = (key, value) => { try { localStorage.setItem(key, value) } catch { /* private mode */ } }
  function here(path, extra) {
    const q = new URLSearchParams(extra || {})
    if (KEEP) q.set('page', KEEP)
    const s = q.toString()
    return path + (s ? '?' + s : '')
  }
  function focusFirst() {
    const el = view.querySelector('[autofocus]') || view.querySelector('input')
    if (el && matchMedia('(hover:hover)').matches) el.focus()
  }
  function setBusy(button, words) {
    if (!button) return () => {}
    const was = button.textContent
    button.disabled = true; button.textContent = words; button.classList.add('waiting')
    return () => { button.disabled = false; button.textContent = was; button.classList.remove('waiting') }
  }
  function problem(where, words) {
    let el = where.querySelector(':scope > .error')
    if (!el) { el = h('p', { class: 'error', role: 'alert' }); where.append(el) }
    el.textContent = words
    el.hidden = !words
  }

  // POST /api/user/info, as Authelia's own page asks it: the GET answers
  // from a stored copy that does not yet know about a passkey just added.
  async function api(method, path, data) {
    const init = { method, credentials: 'same-origin', cache: 'no-store', headers: { accept: 'application/json' } }
    if (data !== undefined) { init.headers['content-type'] = 'application/json'; init.body = JSON.stringify(data) }
    let res
    try { res = await fetch(path, init) } catch { return { status: 0, ok: false, offline: true } }
    let json = null
    try { json = await res.json() } catch { /* an empty or HTML answer */ }
    return { status: res.status, ok: res.ok && (!json || json.status === 'OK'), data: json && json.data, body: json || {} }
  }

  // WebAuthn's options and answers travel as JSON with base64url strings
  // where the browser wants bytes.
  const b64u = {
    decode(s) {
      const pad = '='.repeat((4 - (s.length % 4)) % 4)
      const bin = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'))
      return Uint8Array.from(bin, (c) => c.charCodeAt(0)).buffer
    },
    encode(buf) {
      const bytes = new Uint8Array(buf)
      let bin = ''
      for (const b of bytes) bin += String.fromCharCode(b)
      return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    },
  }
  function requestOptions(o) {
    const out = { ...o, challenge: b64u.decode(o.challenge) }
    if (o.allowCredentials) out.allowCredentials = o.allowCredentials.map((c) => ({ ...c, id: b64u.decode(c.id) }))
    return out
  }
  function creationOptions(o) {
    const out = { ...o, challenge: b64u.decode(o.challenge), user: { ...o.user, id: b64u.decode(o.user.id) } }
    if (o.excludeCredentials) out.excludeCredentials = o.excludeCredentials.map((c) => ({ ...c, id: b64u.decode(c.id) }))
    return out
  }
  function assertionJSON(cred) {
    const r = cred.response
    return {
      id: cred.id, rawId: b64u.encode(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: b64u.encode(r.clientDataJSON), authenticatorData: b64u.encode(r.authenticatorData),
        signature: b64u.encode(r.signature), userHandle: r.userHandle ? b64u.encode(r.userHandle) : undefined,
      },
    }
  }
  function attestationJSON(cred) {
    const r = cred.response
    return {
      id: cred.id, rawId: b64u.encode(cred.rawId), type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: b64u.encode(r.clientDataJSON), attestationObject: b64u.encode(r.attestationObject),
        transports: r.getTransports ? r.getTransports() : undefined,
      },
    }
  }
  // What a person is told when the browser's passkey sheet ends without one.
  function passkeyTrouble(err, creating) {
    const name = err && err.name
    if (name === 'NotAllowedError' || name === 'AbortError') return creating ? 'No passkey was made. You can try again whenever you like.' : 'No passkey was used. Try again, or use your password.'
    if (name === 'InvalidStateError') return creating ? 'This device already has a passkey for your Loam.' : 'That passkey isn’t one this Loam knows.'
    if (name === 'SecurityError') return 'This browser won’t use a passkey on this address.'
    if (name === 'NotSupportedError') return 'This device can’t make a passkey.'
    return creating ? 'The passkey couldn’t be made. Try again.' : 'The passkey didn’t work. Try again, or use your password.'
  }
  let ceremony = null   // the one WebAuthn request in flight, so a second click can end it
  async function withPasskey(kind, options) {
    if (ceremony) ceremony.abort()
    ceremony = new AbortController()
    try {
      return kind === 'create'
        ? await navigator.credentials.create({ publicKey: creationOptions(options), signal: ceremony.signal })
        : await navigator.credentials.get({ publicKey: requestOptions(options), signal: ceremony.signal })
    } finally { ceremony = null }
  }

  function arrived(target) {
    body.classList.add('settle')
    remember(SEEN_KEY, '1')
    setTimeout(() => location.assign(target), matchMedia('(prefers-reduced-motion:reduce)').matches ? 0 : 450)
  }
  const OFFLINE = 'We couldn’t reach your Loam. Check your connection, then try again.'

  // ---------------------------------------------------------------------------
  // Signing in
  // ---------------------------------------------------------------------------

  async function start() {
    const st = await api('GET', '/api/state')
    if (st.offline || !st.data) return showTrouble(st.offline ? OFFLINE : 'The sign-in isn’t answering right now. Try again in a moment.')
    const level = st.data.authentication_level || 0
    if (level === 0) return signIn()
    if (RD && level < 2) return secondFactor(st.data)
    if (RD) {
      const safe = await api('POST', '/api/checks/safe-redirection', { uri: RD })
      if (safe.ok && safe.data && safe.data.ok) return arrived(RD)
      return showTrouble('That link points somewhere this sign-in doesn’t cover, so it won’t take you there.')
    }
    return signedIn(st.data)
  }

  function showTrouble(words) {
    show(h('h1', { text: 'Something’s in the way' }), h('p', { class: 'intro', text: words }),
      h('button', { class: 'primary', type: 'button', text: 'Try again', onclick: () => location.reload() }),
      HOME ? h('a', { class: 'secondary', href: HOME, text: 'Open Loam' }) : null)
  }

  function signIn(opts = {}) {
    const back = remembered(SEEN_KEY) === '1'
    const passkeyFirst = PASSKEYS && !opts.password && remembered(METHOD_KEY) === 'passkey'
    const title = h('h1', { text: back ? 'Welcome back' : 'Sign in to Loam' })
    const intro = h('p', { class: 'intro', text: back ? 'Sign in to pick up where you left off.' : 'Use the username and password you made for your Loam.' })
    const keep = REMEMBER ? h('label', { class: 'keep' },
      h('input', { type: 'checkbox', id: 'keep', checked: remembered('loam.signin.keep') === '1' }), 'Keep me signed in on this device for a month') : null
    const kept = () => !!(keep && keep.querySelector('input').checked)

    const passkeyButton = PASSKEYS ? h('button', { class: passkeyFirst ? 'primary' : 'secondary', type: 'button', text: 'Sign in with a passkey', onclick: () => passkey(passkeyButton) }) : null
    async function passkey(button) {
      problem(view, '')
      const done = setBusy(button, 'Waiting for your passkey')
      try {
        const opt = await api('GET', '/api/firstfactor/passkey')
        if (!opt.ok || !opt.data || !opt.data.publicKey) { problem(view, opt.offline ? OFFLINE : 'Passkeys aren’t available right now. Use your password.'); return }
        let cred
        try { cred = await withPasskey('get', opt.data.publicKey) } catch (err) { problem(view, passkeyTrouble(err, false)); return }
        if (keep) remember('loam.signin.keep', kept() ? '1' : '0')
        const res = await api('POST', '/api/firstfactor/passkey', { response: assertionJSON(cred), keepMeLoggedIn: kept(), targetURL: RD || undefined, requestMethod: RM || undefined })
        if (!res.ok) { problem(view, res.offline ? OFFLINE : 'That passkey didn’t sign you in. Try again, or use your password.'); return }
        remember(METHOD_KEY, 'passkey')
        return after(res)
      } finally { done() }
    }

    const form = h('form', { id: 'signin', autocomplete: 'on', novalidate: true },
      h('label', { for: 'username', text: 'Username' }),
      h('input', { id: 'username', name: 'username', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: true, autofocus: !passkeyFirst }),
      h('label', { for: 'password', text: 'Password' }),
      passwordField('password', 'current-password'),
      keep,
      h('button', { class: 'primary', id: 'go', type: 'submit', text: 'Sign in' }))
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault()
      problem(view, '')
      const username = form.username.value.trim(), password = form.password.value
      if (!username || !password) { problem(view, !username ? 'Type your username.' : 'Type your password.'); (username ? form.password : form.username).focus(); return }
      if (keep) remember('loam.signin.keep', kept() ? '1' : '0')
      const done = setBusy(form.querySelector('#go'), 'Signing in')
      try {
        const res = await api('POST', '/api/firstfactor', { username, password, keepMeLoggedIn: kept(), targetURL: RD || undefined, requestMethod: RM || undefined })
        if (!res.ok) {
          form.password.value = ''; form.password.focus()
          problem(view, res.offline ? OFFLINE : 'That username and password don’t match. Try again.')
          return
        }
        remember(METHOD_KEY, 'password')
        return after(res)
      } finally { done() }
    })

    const reset = RESET ? h('a', { class: 'quiet', href: '/reset-password/step1', text: 'Forgot your password?' }) : null
    if (passkeyFirst) {
      const other = h('button', { class: 'quiet center', type: 'button', text: 'Use your password instead', onclick: () => signIn({ password: true }) })
      show(title, intro, keep, passkeyButton, other)
    } else {
      show(title, intro, form, passkeyButton ? [h('div', { class: 'or', text: 'or' }), passkeyButton] : null, reset)
    }
    focusFirst()
  }

  function passwordField(id, autocomplete, label) {
    const input = h('input', { id, name: id, type: 'password', autocomplete, required: true, 'aria-label': label || null })
    const toggle = h('button', { class: 'show', type: 'button', text: 'Show', 'aria-label': 'Show password', 'aria-pressed': 'false', onclick: () => {
      const showing = input.type === 'password'
      input.type = showing ? 'text' : 'password'
      toggle.textContent = showing ? 'Hide' : 'Show'
      toggle.setAttribute('aria-label', showing ? 'Hide password' : 'Show password')
      toggle.setAttribute('aria-pressed', String(showing))
    } })
    return h('div', { class: 'password' }, input, toggle)
  }

  // After a factor: Authelia says where to go when the target is satisfied;
  // otherwise it is the state that says what is still needed.
  async function after(res) {
    if (res.data && res.data.redirect) return arrived(res.data.redirect)
    return start()
  }

  // The host asks for more than the session has: a passkey or a code from
  // an authenticator app after a password, or the password after a passkey.
  async function secondFactor(state) {
    const info = await api('POST', '/api/user/info')
    const u = info.data || {}
    const title = h('h1', { text: 'One more step' })
    const nodes = [title]
    const targetURL = RD || undefined
    if (!state.factor_knowledge) {
      nodes.push(h('p', { class: 'intro', text: 'This part of your Loam asks for your password as well as your passkey.' }))
      const form = h('form', { novalidate: true }, h('label', { for: 'password', text: 'Password' }), passwordField('password', 'current-password'),
        h('button', { class: 'primary', type: 'submit', text: 'Continue' }))
      form.addEventListener('submit', async (ev) => {
        ev.preventDefault(); problem(view, '')
        const done = setBusy(form.querySelector('button[type=submit]'), 'Checking')
        try {
          const res = await api('POST', '/api/secondfactor/password', { password: form.password.value, targetURL })
          if (!res.ok) { form.password.value = ''; problem(view, res.offline ? OFFLINE : 'That password isn’t right. Try again.'); return }
          return after(res)
        } finally { done() }
      })
      nodes.push(form)
    } else if (u.has_webauthn || u.has_totp) {
      nodes.push(h('p', { class: 'intro', text: 'This part of your Loam asks for a second check.' }))
      if (u.has_webauthn && PASSKEYS) {
        const b = h('button', { class: 'primary', type: 'button', text: 'Use your passkey', onclick: async () => {
          problem(view, '')
          const done = setBusy(b, 'Waiting for your passkey')
          try {
            const opt = await api('GET', '/api/secondfactor/webauthn')
            if (!opt.ok || !opt.data) { problem(view, opt.offline ? OFFLINE : 'Passkeys aren’t available right now.'); return }
            let cred
            try { cred = await withPasskey('get', opt.data.publicKey) } catch (err) { problem(view, passkeyTrouble(err, false)); return }
            const res = await api('POST', '/api/secondfactor/webauthn', { response: assertionJSON(cred), targetURL })
            if (!res.ok) { problem(view, 'That passkey didn’t work. Try again.'); return }
            return after(res)
          } finally { done() }
        } })
        nodes.push(b)
      }
      if (u.has_totp) {
        if (u.has_webauthn && PASSKEYS) nodes.push(h('div', { class: 'or', text: 'or' }))
        nodes.push(totpForm(targetURL))
      }
    } else {
      nodes.push(h('p', { class: 'intro', text: 'This part of your Loam asks for a second check, and you have no passkey yet. Add one in your sign-in settings, then come back.' }),
        h('a', { class: 'primary', href: here('/settings'), text: 'Sign-in settings' }))
    }
    nodes.push(h('button', { class: 'quiet center', type: 'button', text: 'Sign out', onclick: () => signOut() }))
    show(nodes)
    focusFirst()
  }

  function totpForm(targetURL, onDone) {
    const form = h('form', { novalidate: true },
      h('label', { for: 'otp', text: 'Code from your authenticator app' }),
      h('input', { id: 'otp', name: 'otp', class: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '8', pattern: '[0-9]*' }),
      h('button', { class: 'secondary', type: 'submit', text: 'Continue' }))
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault(); problem(form, '')
      const token = form.otp.value.replace(/\s+/g, '')
      if (!/^\d{6,8}$/.test(token)) { problem(form, 'The code is the 6 digits your app shows.'); return }
      const done = setBusy(form.querySelector('button'), 'Checking')
      try {
        const res = await api('POST', '/api/secondfactor/totp', { token, targetURL })
        if (res.status === 429) { problem(form, 'Too many tries. Wait a minute, then try again.'); return }
        if (!res.ok) { form.otp.value = ''; problem(form, res.offline ? OFFLINE : 'That code didn’t work. Codes change every 30 seconds; try the new one.'); return }
        return onDone ? onDone() : after(res)
      } finally { done() }
    })
    return form
  }

  async function signedIn(state) {
    const info = await api('POST', '/api/user/info')
    const name = (info.data && info.data.display_name) || state.username
    show(h('h1', { text: 'You’re signed in' }),
      h('p', { class: 'intro', text: name ? `As ${name}. Your Loam is ready when you are.` : 'Your Loam is ready when you are.' }),
      HOME ? h('a', { class: 'primary', href: HOME, text: 'Open Loam' }) : null,
      h('a', { class: HOME ? 'secondary' : 'primary', href: here('/settings'), text: 'Sign-in settings' }),
      h('button', { class: 'quiet center', type: 'button', text: 'Sign out', onclick: () => signOut() }))
  }

  async function signOut() {
    const res = await api('POST', '/api/logout', RD ? { targetURL: RD } : {})
    if (res.ok && RD && res.data && res.data.safeTargetURL) return location.assign(RD)
    history.replaceState(null, '', here('/'))
    show(h('h1', { text: 'You’re signed out' }), h('p', { class: 'intro', text: res.ok || res.status === 401 ? 'See you soon.' : 'This device may still be signed in. Close the browser to be sure.' }),
      h('a', { class: 'primary', href: here('/'), text: 'Sign in again' }))
  }

  // ---------------------------------------------------------------------------
  // Settings: passkeys and the password
  // ---------------------------------------------------------------------------

  const KEY_ICON = () => {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true')
    s.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M7.5 14.5a4.5 4.5 0 1 1 3.9-6.75L21 7.75v3.5h-2.5v2.5H16v-2.5h-4.6a4.5 4.5 0 0 1-3.9 3.25ZM6.5 10.5h.01"/>'
    return s
  }
  function when(iso) {
    if (!iso) return null
    const d = new Date(iso)
    if (!Number.isFinite(d.getTime()) || d.getFullYear() < 2000) return null
    const day = 864e5, today = new Date(); today.setHours(0, 0, 0, 0)
    const diff = Math.round((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / day)
    if (diff <= 0) return 'today'
    if (diff === 1) return 'yesterday'
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' })
  }
  function deviceName(taken) {
    const ua = navigator.userAgent
    const base = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android phone'
      : /Macintosh/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows PC' : /CrOS/.test(ua) ? 'Chromebook' : /Linux/.test(ua) ? 'Linux computer' : 'This device'
    let name = base, n = 2
    while (taken.has(name.toLowerCase())) name = `${base} ${n++}`
    return name
  }

  async function settings() {
    body.classList.add('wide')
    document.title = 'Sign-in settings · Loam'
    const st = await api('GET', '/api/state')
    if (st.offline || !st.data) return showTrouble(st.offline ? OFFLINE : 'The sign-in isn’t answering right now. Try again in a moment.')
    if ((st.data.authentication_level || 0) === 0) {
      // Sign in first, then come back here: the portal is inside the cookie's
      // domain, so Authelia allows the way back.
      location.replace(here('/', { rd: location.href }))
      return
    }
    const [info, config, creds] = await Promise.all([
      api('POST', '/api/user/info'), api('GET', '/api/configuration'), api('GET', '/api/secondfactor/webauthn/credentials')])
    const user = info.data || {}
    const list = Array.isArray(creds.data) ? creds.data : []
    const name = user.display_name || st.data.username

    const passkeys = h('section', { 'aria-labelledby': 'pk' },
      h('h2', { id: 'pk', text: 'Passkeys' }),
      h('p', { class: 'note', text: 'Sign in with your face, your fingerprint or your device’s screen lock. Nothing to type, nothing to forget.' }),
      list.length ? h('ul', { class: 'list' }, list.map((c) => credentialRow(c))) : h('p', { class: 'empty', text: 'No passkeys yet. Add one on each phone or computer you use.' }),
      PASSKEYS ? h('button', { class: 'primary', type: 'button', id: 'add', text: list.length ? 'Add a passkey for this device' : 'Add a passkey', onclick: (ev) => addPasskey(ev.currentTarget, list) })
        : h('p', { class: 'note', text: 'This browser can’t make passkeys.' }))

    const pwd = config.data && config.data.password_change_disabled ? null : h('section', { 'aria-labelledby': 'pw' },
      h('h2', { id: 'pw', text: 'Password' }),
      h('p', { class: 'note', text: 'For when a passkey isn’t to hand.' }),
      h('button', { class: 'secondary', type: 'button', text: 'Change your password', onclick: (ev) => changePassword(ev.currentTarget) }))

    const out = h('section', { class: 'last' },
      h('button', { class: 'secondary', type: 'button', text: 'Sign out of this device', onclick: () => signOut() }),
      h('a', { class: 'quiet center', href: '/settings?authelia=1', text: 'More options: authenticator apps' }))

    show(HOME ? h('a', { class: 'back', href: HOME, text: '← Back to Loam' }) : null,
      h('h1', { text: 'Sign-in' }),
      h('p', { class: 'intro', text: name ? `How you get into your Loam, ${name}.` : 'How you get into your Loam.' }),
      passkeys, pwd, out)
  }

  function credentialRow(c) {
    const added = when(c.created_at), used = when(c.last_used_at)
    const words = [added ? `Added ${added}` : null, used ? `last used ${used}` : 'not used yet'].filter(Boolean).join(' · ')
    const row = h('li', {}, h('span', { class: 'key' }, KEY_ICON()),
      h('div', { class: 'what' }, h('div', { class: 'name', text: c.description || 'Passkey' }), h('div', { class: 'when', text: words })))
    const remove = h('button', { class: 'act', type: 'button', text: 'Remove', onclick: () => confirmRemove() })
    row.append(remove)
    function confirmRemove() {
      const yes = h('button', { class: 'act', type: 'button', text: 'Remove it', onclick: () => doRemove(yes) })
      const no = h('button', { class: 'act keep-it', type: 'button', text: 'Keep', onclick: () => { yes.remove(); no.remove(); row.append(remove); remove.focus() } })
      remove.remove(); row.append(no, yes); yes.focus()
    }
    async function doRemove(button) {
      const done = setBusy(button, 'Removing')
      const go = () => api('DELETE', `/api/secondfactor/webauthn/credential/${encodeURIComponent(c.id)}`)
      let res = await go()
      if (!res.ok && (res.body.elevation || res.body.authentication)) {
        done()
        if (!(await confirmIdentity(row.closest('section')))) return
        res = await go()
      }
      done()
      if (!res.ok) { problem(row.closest('section'), 'That passkey couldn’t be removed. Try again.'); return }
      settings()
    }
    return row
  }

  async function addPasskey(button, list) {
    const section = button.closest('section')
    problem(section, '')
    const taken = new Set(list.map((c) => (c.description || '').toLowerCase()))
    const done = setBusy(button, 'Getting ready')
    try {
      if (!(await confirmIdentity(section, button))) return
      const description = deviceName(taken)
      const opt = await api('PUT', '/api/secondfactor/webauthn/credential/register', { description })
      if (opt.status === 409) { problem(section, 'You already have a passkey with that name.'); return }
      if (!opt.ok || !opt.data || !opt.data.publicKey) { problem(section, opt.offline ? OFFLINE : 'A passkey can’t be added right now. Try again in a moment.'); return }
      button.textContent = 'Waiting for your device'
      let cred
      try { cred = await withPasskey('create', opt.data.publicKey) } catch (err) { problem(section, passkeyTrouble(err, true)); return }
      const res = await api('POST', '/api/secondfactor/webauthn/credential/register', attestationJSON(cred))
      if (!res.ok) { problem(section, (res.body && res.body.message) || 'Your device made the passkey, but your Loam couldn’t keep it. Try again.'); return }
      remember(METHOD_KEY, 'passkey')
      body.classList.add('settle')
      await settings()
      const fresh = view.querySelector('section')
      if (fresh) fresh.append(h('p', { class: 'done', role: 'status', text: `Your passkey on this ${description === 'This device' ? 'device' : description} is ready. Next time, sign in with it.` }))
    } finally { if (button.isConnected) done() }
  }

  // Managing passkeys asks Authelia for an elevated session. When the
  // session already counts (a second factor this session), nothing is asked.
  // Otherwise: the second factor this person has, or a one-time code Loam
  // keeps for them (Settings › Sign-in in the app shows it: this box sends no
  // mail, so Authelia's "email" lands in a file Loam reads).
  async function confirmIdentity(section, hide) {
    const el = await api('GET', '/api/user/session/elevation')
    if (el.ok && el.data && (el.data.elevated || el.data.skip_second_factor)) return true
    if (!el.ok) { problem(section, el.offline ? OFFLINE : 'Your sign-in couldn’t be checked. Reload the page and try again.'); return false }
    const e = el.data
    const info = (await api('POST', '/api/user/info')).data || {}
    return new Promise((resolve) => {
      const panel = h('div', { class: 'panel', role: 'group', 'aria-labelledby': 'confirm-title' })
      const finish = async (ok) => {
        if (ok) {
          const again = await api('GET', '/api/user/session/elevation')
          ok = !!(again.ok && again.data && (again.data.elevated || again.data.skip_second_factor))
          if (!ok) { problem(panel, 'That didn’t confirm it. Try the code instead.'); return }
        }
        panel.remove(); if (hide) hide.hidden = false; resolve(ok)
      }
      const cancel = h('button', { class: 'quiet', type: 'button', text: 'Cancel', onclick: () => finish(false) })
      const viaCode = () => codeStep()
      function quick() {
        // can_skip_second_factor: a second factor now makes the session count.
        const nodes = [h('h2', { id: 'confirm-title', text: 'First, confirm it’s you' })]
        if (!e.factor_knowledge) {
          nodes.push(h('p', { class: 'note', text: 'You signed in with a passkey. Your password confirms it’s you.' }))
          const f = h('form', { novalidate: true }, h('label', { for: 'confirm-password', text: 'Password' }), passwordField('confirm-password', 'current-password'),
            h('button', { class: 'primary', type: 'submit', text: 'Confirm' }))
          f.addEventListener('submit', async (ev) => {
            ev.preventDefault(); problem(panel, '')
            const done = setBusy(f.querySelector('button[type=submit]'), 'Checking')
            const res = await api('POST', '/api/secondfactor/password', { password: f.querySelector('input').value })
            done()
            if (!res.ok) { problem(panel, 'That password isn’t right. Try again.'); return }
            finish(true)
          })
          nodes.push(f)
        } else {
          nodes.push(h('p', { class: 'note', text: info.has_webauthn ? 'Use a passkey you already have.' : 'Use the code from your authenticator app.' }))
          if (info.has_webauthn && PASSKEYS) {
            const b = h('button', { class: 'primary', type: 'button', text: 'Use a passkey', onclick: async () => {
              problem(panel, '')
              const done = setBusy(b, 'Waiting for your passkey')
              try {
                const opt = await api('GET', '/api/secondfactor/webauthn')
                if (!opt.ok || !opt.data) { problem(panel, 'Passkeys aren’t available right now.'); return }
                let cred
                try { cred = await withPasskey('get', opt.data.publicKey) } catch (err) { problem(panel, passkeyTrouble(err, false)); return }
                const res = await api('POST', '/api/secondfactor/webauthn', { response: assertionJSON(cred) })
                if (!res.ok) { problem(panel, 'That passkey didn’t work. Try again.'); return }
                finish(true)
              } finally { if (b.isConnected) done() }
            } })
            nodes.push(b)
          }
          if (info.has_totp) nodes.push(totpForm(undefined, () => finish(true)))
        }
        nodes.push(h('div', { class: 'links' }, h('button', { class: 'quiet', type: 'button', text: 'Use a one-time code instead', onclick: viaCode }), cancel))
        panel.replaceChildren(...nodes)
      }
      async function codeStep() {
        panel.replaceChildren(h('h2', { id: 'confirm-title', text: 'First, confirm it’s you' }), h('p', { class: 'note waiting', text: 'Making you a one-time code ' }))
        const made = await api('POST', '/api/user/session/elevation')
        if (!made.ok) { panel.replaceChildren(h('h2', { id: 'confirm-title', text: 'First, confirm it’s you' }), h('p', { class: 'error', text: made.offline ? OFFLINE : 'A code couldn’t be made right now. Try again in a minute.' }), cancel); return }
        const f = h('form', { novalidate: true, autocomplete: 'off' },
          h('label', { for: 'otc', text: 'One-time code' }),
          h('input', { id: 'otc', name: 'otc', class: 'code', autocapitalize: 'characters', spellcheck: 'false', autocomplete: 'one-time-code' }),
          h('button', { class: 'primary', type: 'submit', text: 'Confirm' }))
        f.addEventListener('submit', async (ev) => {
          ev.preventDefault(); problem(panel, '')
          const otc = f.otc.value.trim()
          if (!otc) { problem(panel, 'Type the code Loam shows you.'); return }
          const done = setBusy(f.querySelector('button'), 'Checking')
          const res = await api('PUT', '/api/user/session/elevation', { otc })
          done()
          if (!res.ok) { f.otc.value = ''; problem(panel, 'That code didn’t work. Check it, or wait for a new one.'); return }
          finish(true)
        })
        panel.replaceChildren(h('h2', { id: 'confirm-title', text: 'First, confirm it’s you' }),
          h('p', { class: 'note', text: 'Loam has made you a one-time code. Find it in your Loam tab, under Settings › Sign-in, and type it here.' }),
          f, h('div', { class: 'links' }, cancel))
        if (matchMedia('(hover:hover)').matches) f.otc.focus()
      }
      section.append(panel)
      if (hide) hide.hidden = true
      if (e.can_skip_second_factor) quick(); else codeStep()
      panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }

  function changePassword(button) {
    const section = button.closest('section')
    const form = h('form', { class: 'panel', novalidate: true },
      h('label', { for: 'old', text: 'Your password now' }), passwordField('old', 'current-password'),
      h('label', { for: 'new', text: 'A new password' }), passwordField('new', 'new-password'),
      h('label', { for: 'again', text: 'The new password again' }), passwordField('again', 'new-password'),
      h('div', { class: 'row' }, h('button', { class: 'secondary', type: 'button', text: 'Cancel', onclick: () => { form.replaceWith(button); button.focus() } }),
        h('button', { class: 'primary', type: 'submit', text: 'Change it' })))
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault(); problem(form, '')
      const oldp = form.querySelector('#old').value, newp = form.querySelector('#new').value
      if (!oldp || !newp) { problem(form, 'Fill in both passwords.'); return }
      if (newp !== form.querySelector('#again').value) { problem(form, 'The two new passwords aren’t the same.'); return }
      const done = setBusy(form.querySelector('button[type=submit]'), 'Changing')
      const res = await api('POST', '/api/change-password', { old_password: oldp, new_password: newp })
      done()
      if (!res.ok) { problem(form, res.offline ? OFFLINE : (res.body && res.body.message && !/^Unable|Operation failed/i.test(res.body.message) ? res.body.message : 'Your password wasn’t changed. Check the one you have now, then try again.')); return }
      form.replaceWith(h('p', { class: 'done', role: 'status', text: 'Your password is changed. Use the new one next time.' }))
    })
    button.replaceWith(form)
    form.querySelector('#old').focus()
  }

  // ---------------------------------------------------------------------------
  // Which page
  // ---------------------------------------------------------------------------

  const path = location.pathname.replace(/\/+$/, '') || '/'
  view.replaceChildren(h('h1', { text: 'Sign in to Loam' }), h('p', { class: 'intro waiting', text: 'One moment ' }))
  if (path === '/logout') signOut()
  else if (path === '/settings' || path.startsWith('/settings/')) settings()
  else start()
})()
