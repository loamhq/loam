import { h } from './ui.js'
import { manifestForm, manifestTarget, orgLogin } from './integrations.js'
import { formatDate } from './time.js'

// Account mutations use only the account routes. The callback pages keep the
// existing manifest/install return contract; credentials stay with the host.
export function createGithubAccounts({ state, api, changed, reload,
  navigate = url => globalThis.location.assign(url), submit = form => { document.body.appendChild(form); form.submit() } }) {
  const st = { busy: null, confirm: null, error: '', at: null, done: '', links: null, org: '', own: false, options: false, details: new Set() }
  const button = (text, onclick, props = {}) => h('button', { class: 'pillbtn content-mark', type: 'button', text, onclick, disabled: st.busy !== null, ...props })
  const notice = (text, bad = false) => text ? h('p', { class: `note${bad ? ' warn' : ''}`, role: bad ? 'alert' : 'status', text }) : null
  async function step(at, action, run) {
    if (st.busy !== null) return
    st.busy = `${at}:${action}`; st.at = at; st.error = ''; st.done = ''; changed()
    try { await run() } catch (error) { st.error = error.message }
    finally { st.busy = null; changed() }
  }
  function refresh(account) {
    return step(account.account, 'refresh', async () => {
      const out = await api('POST', `/integrations/github/accounts/${encodeURIComponent(account.account)}/refresh`, {})
      state().accounts = out.accounts
      st.done = 'Checked with GitHub just now.'
    })
  }
  function disconnect(account) {
    return step(account.account, 'disconnect', async () => {
      const out = await api('DELETE', `/integrations/github/accounts/${encodeURIComponent(account.account)}`)
      state().accounts = out.accounts; st.confirm = null; st.links = out
      st.done = `${account.account} disconnected.`
    })
  }
  function add(options = {}, at = 'add') {
    return step(at, 'add', async () => {
      const out = await api('POST', '/integrations/github/accounts', options)
      if (out.step === 'install') {
        const url = manifestTarget(out.url)
        if (!url) throw new Error('GitHub did not provide a sign-in address.')
        navigate(url)
      } else if (out.step === 'register') {
        const form = manifestForm(tag => document.createElement(tag), out, { org: options.org ?? null })
        if (!form) throw new Error('GitHub did not provide an app registration address.')
        submit(form)
      } else throw new Error('GitHub did not provide the next sign-in step.')
    })
  }
  function outcome(at) { return st.at === at ? [notice(st.error, true), notice(st.done)] : [] }
  function accountRow(account) {
    const login = account.account, confirming = st.confirm === login
    const status = { authorized: 'Connected', installed: 'Installed', registered: 'Finish connecting', unhealthy: 'Needs attention' }[account.status] ?? account.status
    return h('section', { class: 'github-account-row', key: login, 'aria-label': `GitHub account ${login}` },
      h('div', { class: 'github-account-heading' }, h('div', {}, h('h3', { class: 'setrow-title', text: login }),
        h('p', { class: 'setrow-sub', text: `${{ personal: 'Personal', installation: 'Installation', org: 'Organization' }[account.kind] ?? account.kind} · ${status}` })),
      !confirming ? h('div', { class: 'ghpills' },
        button(st.busy === `${login}:refresh` ? 'Refreshing…' : 'Refresh', () => refresh(account), { 'aria-label': `Refresh ${login}` }),
        button('Disconnect', () => { st.confirm = login; st.at = login; st.error = ''; st.done = ''; changed() }, { 'aria-label': `Disconnect ${login}` })) : null),
      account.status === 'unhealthy' ? notice(account.health?.last_error || 'GitHub could not check this connection.', true) : null,
      account.user?.refresh_expires_at && Number.isFinite(Date.parse(account.user.refresh_expires_at)) ? notice(`Sign in again by ${formatDate(account.user.refresh_expires_at, { year: 'numeric', month: 'short', day: 'numeric' })}`) : null,
      account.status === 'unhealthy' || account.status === 'registered' || account.status === 'installed' && account.kind !== 'installation'
        ? button(st.busy === `${login}:add` ? 'Opening GitHub…' : account.status === 'unhealthy' ? 'Reconnect' : 'Finish connecting', () => add(account.kind === 'org' ? { org: login } : {}, login)) : null,
      account.app?.url || account.manage_url ? h('details', { class: 'github-account-details', open: st.details.has(login), ontoggle: event => { if (event.currentTarget.isConnected) event.currentTarget.open ? st.details.add(login) : st.details.delete(login) } }, h('summary', { text: 'Connection details' }),
        account.installation ? notice(account.installation.repository_selection === 'all' ? 'All repositories' : 'Selected repositories') : null,
        ...[[account.app?.url, 'GitHub App'], [account.manage_url, 'Manage access on GitHub']].map(([href, text]) => manifestTarget(href) ? h('a', { class: 'quietlink', href, target: '_blank', rel: 'noopener noreferrer', text }) : null)) : null,
      ...outcome(login),
      confirming ? h('div', { class: 'github-account-confirm' },
        h('p', { class: 'note', text: `Disconnect ${login}? Agents will lose access through this account.` }),
        account.kind === 'personal' && account.disconnect_also?.length ? h('p', { class: 'note', text: `Also disconnects: ${account.disconnect_also.join(', ')}. They share this GitHub App.` }) : null,
        h('div', { class: 'ghpills' }, button('Keep account', () => { st.confirm = null; changed() }),
          button(st.busy === `${login}:disconnect` ? 'Disconnecting…' : 'Disconnect account', () => disconnect(account), { class: 'pillbtn primary danger' }))) : null)
  }
  function view() {
    const gh = state()
    return [gh.accountsError ? [notice(gh.accountsError, true), button('Try again', reload)] : null,
      gh.accounts === null && !gh.accountsError ? notice('Loading GitHub accounts…') : null,
      st.links ? h('div', { class: 'github-account-links', role: 'status' },
        notice(`${st.links.account} disconnected. To finish on GitHub:`),
        ...[['uninstall_url', 'Uninstall on GitHub'], ['revoke_url', 'Revoke access on GitHub'], ['delete_app_url', 'Delete app on GitHub']].map(([key, text]) => manifestTarget(st.links[key]) ? h('a', { class: 'quietlink', href: st.links[key], target: '_blank', rel: 'noopener noreferrer', text }) : null),
        button('Done', () => { st.links = null; changed() })) : null,
      Array.isArray(gh.accounts) ? h('div', { class: 'github-account-list' }, ...gh.accounts.map(accountRow)) : null,
      Array.isArray(gh.accounts) && !gh.accounts.length ? notice('No GitHub accounts connected.') : null,
      h('div', { class: 'github-account-add' }, button(st.busy === 'add:add' ? 'Opening GitHub…' : 'Add account', () => add(), { class: `pillbtn primary${st.confirm ? ' quiet-primary' : ''}` }),
        ...outcome('add'), h('details', { open: st.options, ontoggle: event => { st.options = event.currentTarget.open } }, h('summary', { text: 'Organization options' }),
          h('form', { onsubmit: event => {
            event.preventDefault()
            const org = orgLogin(st.org)
            if (org) add({ org, ...(st.own ? { own_app: true } : {}) })
            else { st.at = 'add'; st.error = 'Enter the organization’s GitHub login.'; changed() }
          } }, h('label', { for: 'github-account-org', text: 'Organization' }),
          h('input', { id: 'github-account-org', class: 'field-input', value: st.org, autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', oninput: event => { st.org = event.target.value } }),
          h('label', { class: 'github-own-app' }, h('input', { type: 'checkbox', checked: st.own, onchange: event => { st.own = event.target.checked } }), 'Register its own GitHub App'),
          h('button', { class: 'pillbtn content-mark', type: 'submit', disabled: st.busy !== null, text: 'Continue to GitHub' }))))]
  }
  return { st, view, refresh, disconnect, add }
}
