import { acmeView, acmeNotice, acmeAccounts, ACME_TOKENS_URL } from './view.js'
import { copyText } from '/clipboard.js'

// Page companion for the host-owned plugin. The catalog alone activates it.
export function activate(loam) {
  const { h, icon, kept, settingsRow, menuSurface } = loam.ui
  loam.ui.styles.add('page/style.css')
  const local = { acme: { loaded: false, data: null, busy: null, error: '', done: '', confirm: false, revoke: null } }
  const context = loam.ui.extension({ state: local })
  const host = context.host
  const { settingsHead, settingsBack, tile } = host
  const goSettings = (...args) => host.goSettings(...args)
  let disposed = false
  const requests = new AbortController()
  async function request(method, path, body) {
    const res = await fetch(path, { method, cache: 'no-store', credentials: 'same-origin', signal: requests.signal,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body) })
    const data = await res.json()
    if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status}).`), { status: res.status, body: data })
    return data
  }
  // ------------------------------------------------------------ Acme
  //
  // Device-code sign-in first; the alternative is a personal
  // access token, pasted into the Connect sheet and sent to the plugin's `PUT`,
  // which checks it with Acme and keeps it on the host. It goes nowhere else
  // — not to this page's storage, not to the console, not into `state` — and the
  // field is emptied the moment the `PUT` answers, whatever it answered.

  function returnToAcme() {
    goSettings('integration', 'acme')
    if (host.state.acme.data?.account) host.state.sheet.account = host.state.acme.data.account.id
  }

  async function loadAcme() {
    try {
      const data = await request('GET', '/x/acme/')
      if (disposed) return
      host.state.acme = { ...host.state.acme, loaded: true, data }
    } catch (err) {
      if (disposed) return
      host.state.acme = { ...host.state.acme, loaded: true, error: err.message }
    }
    host.schedule()
  }

  function keepAcme(data, over = {}) {
    if (disposed) return
    if (data && typeof data === 'object' && typeof data.status === 'string') {
      host.state.acme = { ...host.state.acme, loaded: true, data }
    }
    Object.assign(host.state.acme, over)
    host.schedule()
  }

  // One pill's step on the card: a refusal is the plugin's sentence, in the
  // warning ink, with the pills still there.
  async function acmeStep(busy, run) {
    if (disposed) return
    host.state.acme = { ...host.state.acme, busy, error: '', done: '' }
    host.schedule()
    try {
      await run()
    } catch (err) {
      if (disposed) return
      host.state.acme = { ...host.state.acme, error: err.message }
      await loadAcme()
    } finally {
      if (disposed) return
      host.state.acme = { ...host.state.acme, busy: null }
      host.schedule()
    }
  }

  function refreshAcme() {
    return acmeStep('refresh', async () => {
      keepAcme(await request('POST', '/x/acme/refresh', {}), { done: 'Checked with Acme just now.' })
    })
  }

  // The host can revoke a login itself. A token may still need a final visit.
  function disconnectAcme() {
    return acmeStep('disconnect', async () => {
      const answer = await request('DELETE', '/x/acme/')
      const revoke = { revoked: answer.revoked === true, href: typeof answer?.revoke_url === 'string' && answer.revoke_url !== '' ? answer.revoke_url : ACME_TOKENS_URL }
      keepAcme(answer, { confirm: false, revoke })
      await loadAcme()
    })
  }

  // Only the open sign-in sheet owns a poll. Serial timeouts keep slow requests
  // from overlapping; sheet identity prevents a late answer reopening a sheet.
  let acmePoll = null

  function stopAcmePoll() {
    clearTimeout(acmePoll)
    acmePoll = null
  }

  function queueAcmePoll(sheet) {
    stopAcmePoll()
    if (host.state.sheet !== sheet || !sheet.login?.pending || sheet.busy) return
    acmePoll = setTimeout(async () => {
      acmePoll = null
      if (disposed || host.state.sheet !== sheet) return
      try {
        const data = await request('GET', '/x/acme/')
        if (disposed || host.state.sheet !== sheet) return
        keepAcme(data)
        sheet.login = data.login
        sheet.error = data.health?.last_error ?? ''
        if (data.status === 'connected' && !data.login) return returnToAcme()
      } catch (err) {
        if (disposed || host.state.sheet !== sheet) return
        sheet.error = err.message
      }
      host.schedule()
      queueAcmePoll(sheet)
    }, 3000)
  }

  function openAcmeLogin() {
    host.state.acme = { ...host.state.acme, error: '', done: '' }
    host.openSheet({ kind: 'acme-login', login: null, error: '', busy: false, copied: null })
    startAcmeLogin()
  }

  async function startAcmeLogin(accountId = null) {
    const sheet = host.state.sheet
    if (sheet?.kind !== 'acme-login' || sheet.busy) return
    stopAcmePoll()
    sheet.busy = true
    sheet.error = ''
    host.schedule()
    try {
      const answer = await request('POST', '/x/acme/login', accountId === null ? {} : { account_id: accountId })
      if (disposed || host.state.sheet !== sheet) return
      if (answer.status === 'connected') {
        keepAcme(answer, { error: '', done: '', confirm: false, revoke: null })
        return returnToAcme()
      }
      sheet.login = answer
    } catch (err) {
      if (disposed || host.state.sheet !== sheet) return
      const accounts = acmeAccounts(err.body)
      sheet.login = accounts ? { pending: false, accounts } : null
      sheet.error = accounts ? '' : err.message
    } finally {
      sheet.busy = false
      if (host.state.sheet === sheet) {
        host.schedule()
        queueAcmePoll(sheet)
      }
    }
  }

  // The token, held between a several-accounts refusal and the account they pick
  // — that one step and no longer. A module variable and not `state`, so it is
  // in nothing that is drawn, and forgotten whenever the sheet goes.
  let acmeHeld = null

  function forgetAcmeToken() {
    acmeHeld = null
    acmeField.value = ''
    acmeField.setAttribute('type', 'password')
  }

  function openAcmeSheet() {
    forgetAcmeToken()
    host.state.acme = { ...host.state.acme, error: '', done: '' }
    host.openSheet({ kind: 'acme', accounts: null, error: '', busy: false, shown: false })
    setTimeout(() => acmeField.focus?.(), 0)
  }

  // *Connect*: the token as typed, alone. With `accountId`, the token held from
  // the refusal that named the accounts, and the one they chose.
  async function connectAcme(accountId = null) {
    const sheet = host.state.sheet
    if (sheet?.kind !== 'acme' || sheet.busy) return
    const token = accountId === null ? acmeField.value.trim() : acmeHeld
    if (!token) {
      sheet.error = accountId === null ? 'Paste the token first.' : 'The token is no longer held here; paste it again.'
      sheet.accounts = accountId === null ? sheet.accounts : null
      return host.schedule()
    }
    sheet.busy = true
    sheet.error = ''
    host.schedule()
    let answer = null
    let refused = null
    try {
      answer = await request('PUT', '/x/acme/', accountId === null ? { token } : { token, account_id: accountId })
    } catch (err) {
      refused = err
    } finally {
      // Answered, whatever the answer: the field is emptied and the token is
      // held for the next step only if the plugin asked for an account.
      acmeHeld = null
      acmeField.value = ''
      acmeField.setAttribute('type', 'password')
      sheet.busy = false
      sheet.shown = false
    }
    if (disposed || host.state.sheet !== sheet) return
    if (!refused) {
      keepAcme(answer, { error: '', done: '', confirm: false, revoke: null })
      returnToAcme()
      return
    }
    const accounts = accountId === null ? acmeAccounts(refused.body) : null
    if (accounts) {
      acmeHeld = token
      sheet.accounts = accounts
      sheet.error = ''
    } else {
      sheet.accounts = null
      sheet.error = refused.message
    }
    host.schedule()
  }

  // Acme's personal access token. A password field, so it is not on the
  // screen unless asked for, and nothing a phone would do to a word: no
  // autocorrect, no capital, no spelling, no remembering. Enter connects.
  const acmeField = h('input', {
    id: 'bc-token',
    class: 'bctoken',
    type: 'password',
    placeholder: 'Paste the token here',
    autocomplete: 'off',
    autocapitalize: 'off',
    autocorrect: 'off',
    spellcheck: 'false',
    onkeydown: (e) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        connectAcme()
      }
    },
  })
  // Acme's card, in GitHub's style: the mark and name; Not connected or who
  // and where; one quiet line; what Loam uses it for; and the pills that apply.
  function acmeCard({ heading = true } = {}) {
    const bc = host.state.acme
    const view = acmeView(bc.data)
    const head = heading ? h(
      'div',
      { class: 'provider-hero' },
      tile(acmeMark(20), { kind: 'glyph acme' }),
      h('div', { class: 'setrow-title', text: 'Acme' }),
    ) : null
    if (!view) {
      return h(
        'div',
        { class: 'card integration acme', key: 'acme' },
        head,
        h('div', {
          class: bc.error ? 'signline warn' : 'note',
          text: bc.error || (bc.loaded ? 'The plugin gave no answer.' : 'Asking the plugin…'),
        }),
      )
    }
    return h(
      'div',
      { class: 'card integration acme', key: 'acme' },
      head,
      h(
        'div',
        { class: 'ghlines' },
        ...view.lines.map((line) =>
          h(
            'div',
            { class: 'signline ghline bcline', key: line.key },
            line.text,
            line.account
              ? line.account.href
                ? h('a', { class: 'bcaccount', href: line.account.href, target: '_blank', rel: 'noopener noreferrer', text: line.account.text })
                : line.account.text
              : null,
          ),
        ),
      ),
      view.quiet ? h('div', { class: `note ghquiet${view.quiet.tone === 'warn' ? ' warn' : ''}`, text: view.quiet.text }) : null,
      h('div', { class: 'note ghuses', text: view.uses }),
      bc.error ? h('div', { class: 'note warn gherror', role: 'status', text: bc.error }) : null,
      bc.done && !bc.error ? h('div', { class: 'note ghdone', text: bc.done }) : null,
      bc.revoke ? acmeRevoke(bc.revoke) : bc.confirm ? acmeConfirm() : acmePills(view),
      view.actions.includes('signin') && !bc.revoke && !bc.confirm
        ? h('button', { class: 'quietlink bctokenway', type: 'button', disabled: bc.busy !== null, text: 'Paste a token instead', onclick: host.openAcmeSheet })
        : null,
    )
  }

  const BC_PILLS = {
    signin: { label: 'Sign in to Acme', going: 'Signing in…', run: () => openAcmeLogin() },
    refresh: { label: 'Refresh', going: 'Refreshing…', run: () => host.refreshAcme() },
    disconnect: {
      label: 'Disconnect',
      going: 'Disconnecting…',
      run: () => {
        host.state.acme = { ...host.state.acme, confirm: true, error: '', done: '' }
        host.schedule()
      },
    },
  }

  function acmePills(view) {
    const busy = host.state.acme.busy
    return h(
      'div',
      { class: 'ghpills' },
      ...view.actions.map((id) =>
        h('button', {
          class: id === 'signin' ? 'pillbtn primary ghpill' : `pillbtn content-mark ghpill${id === 'disconnect' ? ' danger' : ''}`,
          key: id,
          type: 'button',
          disabled: busy !== null,
          text: busy === id ? BC_PILLS[id].going : BC_PILLS[id].label,
          onclick: BC_PILLS[id].run,
        }),
      ),
    )
  }

  function acmeConfirm() {
    const busy = host.state.acme.busy === 'disconnect'
    return h(
      'div',
      { class: 'loginflow ghconfirm' },
      h('div', { class: 'signline', text: 'Disconnect Acme? The agents stop reading and writing Acme until it is connected again.' }),
      h(
        'div',
        { class: 'ghpills' },
        h('button', { class: 'pillbtn primary ghpill danger', type: 'button', disabled: busy, text: busy ? 'Disconnecting…' : 'Disconnect', onclick: host.disconnectAcme }),
        h('button', {
          class: 'pillbtn content-mark ghpill',
          type: 'button',
          disabled: busy,
          text: 'Keep it',
          onclick: () => {
            host.state.acme = { ...host.state.acme, confirm: false }
            host.schedule()
          },
        }),
      ),
    )
  }

  // The last step, which only they can take: Acme still honours the token
  // until they revoke it there.
  function acmeRevoke({ revoked, href }) {
    return h(
      'div',
      { class: 'loginflow ghlinks' },
      h('div', { class: 'signline', text: revoked ? 'Signed out of Acme' : 'Loam has forgotten the token. Acme still honours it until you revoke it there:' }),
      revoked ? null : h('a', { class: 'loginlink', href, target: '_blank', rel: 'noopener noreferrer', text: 'Revoke the token in Acme' }),
      h('button', {
        class: 'pillbtn content-mark ghpill',
        type: 'button',
        text: 'Done',
        onclick: () => {
          host.state.acme = { ...host.state.acme, revoke: null }
          host.schedule()
        },
      }),
    )
  }

  function acmeCopy(sheet, which, value) {
    return h('button', {
      class: 'pillbtn content-mark devcopy', type: 'button', key: `copy-${which}`,
      'aria-label': which === 'url' ? 'Copy the link' : 'Copy the code',
      text: sheet.copied === which ? 'Copied' : 'Copy',
      onclick: async () => {
        if (!(await copyText(value)) || host.state.sheet !== sheet) return
        sheet.copied = which
        host.schedule()
        setTimeout(() => {
          if (host.state.sheet === sheet && sheet.copied === which) {
            sheet.copied = null
            host.schedule()
          }
        }, 2000)
      },
    })
  }

  function acmeLoginSheet(sheet) {
    const login = sheet.login
    const accounts = acmeAccounts(login)
    return h(
      'div', { class: 'sheet acme-login' },
      host.sheetTop(),
      settingsHead('Sign in to Acme', null),
      h('div', { class: 'loginflow device' },
        sheet.busy ? h('div', { class: 'note', text: 'Starting the sign-in…' }) : null,
        accounts
          ? h('div', { class: 'bcaccounts loginflow' },
            h('div', { class: 'signline', text: 'Choose the account Loam should use' }),
            ...accounts.map((a) => h('button', {
              class: 'pillbtn content-mark ghpill bcpick', key: `acct:${a.id}`, type: 'button',
              disabled: sheet.busy, text: a.name ?? String(a.id), onclick: () => host.startAcmeLogin(a.id),
            })))
          : login?.pending
            ? h('div', { class: 'loginflow' },
              h('div', { class: 'devrow' },
                h('div', { class: 'kicker', text: 'Open' }),
                h('a', { class: 'loginlink', href: login.verification_url, target: '_blank', rel: 'noopener noreferrer', text: login.verification_url }),
                acmeCopy(sheet, 'url', login.verification_url)),
              h('div', { class: 'devrow' },
                h('div', { class: 'kicker', text: 'Enter this code' }),
                h('div', { class: 'devcode', text: login.user_code }),
                acmeCopy(sheet, 'code', login.user_code)),
              h('div', { class: 'note', text: 'Open the link on any device, sign in to Acme, and enter the code. This page notices by itself.' }))
            : null,
        sheet.error ? h('div', { class: 'note warn bcerror', role: 'status', text: sheet.error }) : null,
        h('div', { class: 'loginbtns' },
          !sheet.busy && !login?.pending && !accounts
            ? h('button', { class: 'primary bcretry', type: 'button', text: 'Try again', onclick: () => host.startAcmeLogin() })
            : null,
          h('button', { class: 'cancel content-mark bccancel', type: 'button', text: 'Cancel', onclick: () => goSettings('integrations') })),
      ),
    )
  }

  // The Connect sheet: where the token comes from, the one field, and Connect.
  // After a several-accounts refusal, the accounts to choose from instead of the
  // field; after any other refusal, the plugin's sentence in the warning ink and
  // the field empty again.
  function acmeSheet(sheet) {
    const where = h(
      'div',
      { class: 'note bcwhere' },
      'In Acme, open your profile and create a ',
      h('a', { class: 'loginlink', href: ACME_TOKENS_URL, target: '_blank', rel: 'noopener noreferrer', text: 'personal access token' }),
      ' under Authentication, then paste it here.',
    )
    const error = sheet.error ? h('div', { class: 'note warn bcerror', role: 'status', text: sheet.error }) : null
    if (sheet.accounts) {
      return h(
        'div',
        { class: 'sheet acme' },
        host.sheetTop(),
        settingsHead('Connect Acme', null),
        h('div', { class: 'signline', text: 'This token reaches more than one Acme account. Choose the one Loam should use:' }),
        h(
          'div',
          { class: 'loginflow bcaccounts' },
          ...sheet.accounts.map((a) =>
            h('button', {
              class: 'pillbtn content-mark ghpill bcpick',
              key: `acct:${a.id}`,
              type: 'button',
              disabled: sheet.busy,
              text: a.name ?? String(a.id),
              onclick: () => host.connectAcme(a.id),
            }),
          ),
        ),
        error,
        h('button', { class: 'pillbtn content-mark ghpill', type: 'button', disabled: sheet.busy, text: 'Cancel', onclick: () => goSettings('integrations') }),
      )
    }
    return h(
      'div',
      { class: 'sheet acme' },
      host.sheetTop(),
      settingsHead('Connect Acme', null),
      where,
      h(
        'div',
        { class: 'field flat bcfield' },
        kept(host.acmeField),
        h('button', {
          class: 'iconbtn content-mark bcshow',
          type: 'button',
          text: sheet.shown ? 'Hide' : 'Show',
          'aria-label': sheet.shown ? 'Hide the token' : 'Show the token',
          onclick: () => {
            sheet.shown = !sheet.shown
            host.acmeField.setAttribute('type', sheet.shown ? 'text' : 'password')
            host.schedule()
          },
        }),
      ),
      error,
      h('button', { class: 'primary bcconnect', type: 'button', disabled: sheet.busy, text: sheet.busy ? 'Connecting…' : 'Connect', onclick: () => host.connectAcme() }),
    )
  }
  function acmeMark(size = 20) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    for (const [key, value] of Object.entries({ width: size, height: size, viewBox: '0 0 24 24', fill: 'currentColor', 'aria-hidden': 'true', class: 'mark', 'data-mark': 'acme' })) svg.setAttribute(key, value)
    svg.style.flex = 'none'
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path')
    path.setAttribute('d', 'M12 1a11 11 0 1 1 0 22a11 11 0 1 1 0-22zM12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM5.1 15.6l-1.6-1.2c2.1-2.9 3.9-5 5.6-5 1.5 0 2.5 1.2 3.4 2.5.8 1.1 1.3 1.7 2.3 1.7 1.3 0 2.9-.6 5-1.7l.9 1.8c-2.4 1.3-4.3 1.9-5.9 1.9-2 0-3-1.3-3.9-2.6-.7-1-1.2-1.6-1.8-1.6-.9 0-2.2 1.3-4 4z')
    svg.append(path)
    return svg
  }

  context.attach({ refreshAcme, disconnectAcme, openAcmeLogin, startAcmeLogin, openAcmeSheet, connectAcme, acmeField })
  function summary() {
    const bc = local.acme, connected = ['connected', 'unhealthy'].includes(bc.data?.status)
    return { connected, summary: bc.error && !bc.data ? 'Connection unavailable'
      : !bc.loaded && !bc.data ? 'Loading…' : connected && bc.data?.account ? '1 account' : 'Not connected' }
  }
  loam.places.settings.section.add({ id: 'acme', group: 'integrations', title: 'Acme', icon: acmeMark,
    summary, notice: () => acmeNotice(local.acme.data), render: ({ sheet }) => {
      const account = local.acme.data?.account
      const back = [settingsBack('Integrations', 'integrations'), settingsHead('Acme')]
      if (!account) return [...back, acmeCard({ heading: false })]
      if (String(sheet.account) === String(account.id)) return [
        h('button', { class: 'backbtn', onclick: () => goSettings('integration', 'acme') }, icon.chevronLeft(18), 'Acme'),
        settingsHead(account.name || String(account.id)), acmeCard({ heading: false })]
      return [...back, h('div', { class: 'rows' }, settingsRow({ key: account.id, title: account.name || String(account.id), sub: 'Connected', onclick: () => {
        goSettings('integration', 'acme'); host.state.sheet.account = account.id
      } }))]
    } })
  loam.places.app.overlay.add({ id: 'acme.sheets', slot: 'sheet', render: ({ sheet }) => {
    if (!['acme', 'acme-login'].includes(sheet.kind)) return null
    const close = host.closeSheet
    const body = sheet.kind === 'acme' ? acmeSheet(sheet) : acmeLoginSheet(sheet)
    const title = sheet.kind === 'acme' ? 'Connect Acme' : 'Sign in to Acme'
    const surface = menuSurface(body, { title, close, width: 480, heading: false })
    const node = host.state.wide ? h('div', { class: `dialog ${sheet.kind}`, onclick: e => { if (e.target === e.currentTarget) close() } }, surface) : surface
    node.__key = `sheet:${sheet.kind}`
    return [h('button', { class: 'scrim', key: 'scrim', title: 'Close', 'aria-label': 'Close', onclick: close }), node]
  } })
  loam.events.on('sheet.changing', ({ sheet }) => {
    stopAcmePoll()
    if (sheet?.kind !== 'acme') forgetAcmeToken()
  })
  loam.events.on('settings.opened', ({ opening }) => {
    if (opening) { local.acme = { ...local.acme, error: '', done: '', confirm: false, revoke: null }; loadAcme() }
  })
  const cleanup = () => {
    if (disposed) return
    disposed = true; requests.abort(); stopAcmePoll(); forgetAcmeToken()
    if (['acme', 'acme-login'].includes(host.state.sheet?.kind)) host.closeSheet()
    else if (host.state.sheet?.page === 'integration' && host.state.sheet.provider === 'acme') goSettings('integrations')
    local.acme = { loaded: false, data: null, error: '', busy: null }
    host.schedule()
  }
  loam.events.on('page.leaving', cleanup)
  loadAcme()
  return cleanup
}
