import { createProviderSignIn } from "../provider-sign-in.js"
import { copyText } from "../clipboard.js"
import { h } from "../ui.js"
import { manifestForm, orgLogin, orgApps, returnFrom, installOrg } from "../integrations.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {



// ------------------------------------------------------------ GitHub
//
// The first integration, and the flow has **nothing pasted in it**: the page
// asks the broker for a manifest and posts it to GitHub as a form the browser
// submits, GitHub sends the browser back to one of two paths on this app, and
// the page hands the query to the broker. Every answer is the state, and the
// card is drawn from it. See integrations.js for what the card says.

async function loadGithub() {
  await Promise.all([loadGithubState(), loadGithubAccounts()])
}

async function loadGithubAccounts() {
  try {
    const out = await host.api('GET', '/integrations/github/accounts')
    if (!Array.isArray(out?.accounts)) throw new Error('GitHub accounts could not be read from this host.')
    host.state.github = { ...host.state.github, accounts: out.accounts, accountsError: '' }
  } catch (err) {
    if ([404, 405].includes(err.status)) {
      delete host.state.github.accounts
      host.state.github.accountsError = ''
    } else host.state.github = { ...host.state.github, accounts: host.state.github.accounts ?? null, accountsError: err.message }
  }
  host.schedule()
}

async function loadGithubState() {
  try {
    const data = await host.api('GET', '/integrations/github')
    host.state.github = { ...host.state.github, loaded: true, data, missing: false }
  } catch (err) {
    // A broker from before the integration has no such route. That is "not
    // offered yet", and not a fault on the card.
    host.state.github = { ...host.state.github, loaded: true, missing: err.status === 404 || err.status === 405, error: err.status === 404 || err.status === 405 ? '' : err.message }
  }
  host.schedule()
}



// One step of the flow: `busy` names the pill that is going, and a refusal is
// the broker's sentence on the card with the pills still there to retry.
async function githubStep(busy, run, at = 'github') {
  host.state.github = { ...host.state.github, busy, error: '', done: '', at }
  host.schedule()
  try {
    await run()
  } catch (err) {
    host.state.github = { ...host.state.github, error: err.message }
    // What the state is now, so the pills offered for the retry are the right
    // ones: a bad `state` on the way back leaves the broker where it was.
    await loadGithub()
  } finally {
    host.state.github = { ...host.state.github, busy: null }
    host.schedule()
  }
}

function keepGithub(data, over = {}) {
  if (data && typeof data === 'object' && typeof data.status === 'string') {
    host.state.github = { ...host.state.github, loaded: true, missing: false, data }
  }
  Object.assign(host.state.github, over)
  host.schedule()
}



// *Set up on GitHub*: the manifest, posted from this browser to GitHub's own
// create page. The page goes with it, so nothing after `submit()` runs.
function setupGithub() {
  return githubStep('setup', async () => {
    const answer = await host.api('POST', '/integrations/github/manifest', {})
    const form = manifestForm((tag) => document.createElement(tag), answer ?? {})
    if (!form) throw new Error('The broker named somewhere other than GitHub to post the manifest to, so nothing was sent.')
    document.body.appendChild(form)
    form.submit()
  })
}



// *Connect an organization*: the same manifest flow, for an app the
// organization owns. The broker names the organization's own create page —
// GitHub's "Create GitHub App for ‹org›" — and the form goes nowhere else
// (`orgManifestTarget()`), because the first app is what an `org` the host
// ignored would replace. Only an owner of the organization can click Create
// there; GitHub comes back through `/integrations/github/registered` as for
// the first app, and the state says which app it was.
function connectOrg() {
  const org = orgLogin(orgField.value)
  const name = orgAppField.value.trim()
  if (!org) {
    host.state.github = { ...host.state.github, at: 'connect', error: 'That is not an organization’s login on GitHub: letters, digits and hyphens, as in its address, github.com/‹login›.', done: '' }
    host.schedule()
    return
  }
  return githubStep(
    'connect',
    async () => {
      const answer = await host.api('POST', '/integrations/github/manifest', name ? { org, name } : { org })
      const form = manifestForm((tag) => document.createElement(tag), answer ?? {}, { org })
      if (!form) throw new Error(`The broker did not name ${org}’s own create page on GitHub, so nothing was sent. A broker that predates organizations’ apps has to be restarted first.`)
      document.body.appendChild(form)
      form.submit()
    },
    'connect',
  )
}



// *Install* and *Reconnect*: GitHub's install page, which — the app asking for
// the user's authorization on install — sends the browser back to
// `/integrations/github/installed`. An install URL the register answer already
// carried is used as it is, once. With `org`, that organization's app.
function installGithub(busy = 'install', org = null) {
  return githubStep(
    org === null ? busy : `${org}:${busy}`,
    async () => {
      let held = null
      if (org === null) {
        held = host.state.github.installUrl
        host.state.github = { ...host.state.github, installUrl: null }
      } else if (host.state.github.orgInstall?.org === org) {
        held = host.state.github.orgInstall.url
        host.state.github = { ...host.state.github, orgInstall: null }
      }
      const url = held ?? (await host.api('POST', '/integrations/github/install', org === null ? {} : { org }))?.url
      if (typeof url !== 'string' || url === '') throw new Error('The broker answered no install address.')
      location.assign(url)
    },
    org ?? 'github',
  )
}

function refreshGithub() {
  return githubStep('refresh', async () => {
    keepGithub(await host.api('POST', '/integrations/github/refresh', {}), { done: 'Checked with GitHub just now.' })
  })
}



// *Disconnect* asks once, and then the three things only they can do on GitHub
// are the last step: the host forgets, GitHub does not until they say so there.
function disconnectGithub() {
  return githubStep('disconnect', async () => {
    // The organizations' apps go too, and the host's answer names only the
    // first app's pages; each org app's own page is kept from before, so the
    // last step can say where those are.
    const orgs = orgApps(host.state.github.data).map((o) => ({ org: o.org, url: o.app?.url }))
    const answer = await host.api('DELETE', '/integrations/github')
    host.state.github = { ...host.state.github, confirm: false, links: { ...(answer ?? {}), orgs } }
    await loadGithub()
  })
}



// Coming back from GitHub. The path and its query are read once, the address
// bar is put back to `/` so a reload does not hand a used code over again, and
// Settings opens on Integrations with the card in whatever the broker says.
function githubReturn() {
  const back = returnFrom(globalThis.location?.pathname, globalThis.location?.search)
  if (!back) return false
  history.replaceState(null, '', '/')
  host.state.sheet = { kind: 'settings', page: 'integration', provider: 'github', account: 'personal' }
  host.loadProviders()
  githubStep(back.route === 'register' ? 'setup' : 'install', async () => {
    if (back.body.state === null) {
      throw new Error('GitHub’s answer reached this page without its state, so there was nothing to hand the broker. Try again from here.')
    }
    const answer = await host.api('POST', `/integrations/github/${back.route}`, back.body)
    await loadGithubAccounts()
    const install = answer?.install && typeof answer.install.url === 'string' ? answer.install.url : null
    // An organization's app: its card offers the address, whatever the first
    // app's state, and says what is next on it.
    const org = installOrg(install, answer)
    if (org !== null) {
      keepGithub(answer, { orgInstall: { org, url: install }, at: org, done: `Created on GitHub. Install it on ${org} next.` })
      host.state.sheet.account = org
    } else {
      keepGithub(answer, { installUrl: install })
      if (answer?.installed || answer?.user) host.state.sheet.account = 'personal'
    }
  })
  return true
}



// Provider routes and flow kinds come from the catalog. The card owns its
// temporary code field; the host owns the login and settled account reading.
const providerSignIn = createProviderSignIn({
  api: host.api, changed: host.schedule, copy: copyText, refresh: host.loadProviders,
  update: (id, login, done) => {
    host.state.providers = host.state.providers.map((p) => p.id === id ? {
      ...p, login, connected: login.connected ?? (done ? true : p.connected),
      reason: Object.hasOwn(login, 'reason') ? login.reason : done ? null : p.reason,
    } : p)
  },
})



// Connect an organization: its login on GitHub, and a name for the app if not
// the host's own ("Loam for ‹org›"). Enter in either goes to GitHub.
const connectKeys = (e) => {
  if (e.key === 'Enter') {
    e.preventDefault()
    connectOrg()
  }
}

const orgField = h('input', { id: 'gh-org', placeholder: 'Organization, as in github.com/‹login›', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', onkeydown: connectKeys })

const orgAppField = h('input', { id: 'gh-org-app', placeholder: 'App name (optional)', autocomplete: 'off', onkeydown: connectKeys })



return Object.defineProperties({}, {
    loadGithub: { enumerable: true, get: () => loadGithub },
    githubStep: { enumerable: true, get: () => githubStep },
    keepGithub: { enumerable: true, get: () => keepGithub },
    setupGithub: { enumerable: true, get: () => setupGithub },
    connectOrg: { enumerable: true, get: () => connectOrg },
    installGithub: { enumerable: true, get: () => installGithub },
    refreshGithub: { enumerable: true, get: () => refreshGithub },
    disconnectGithub: { enumerable: true, get: () => disconnectGithub },
    githubReturn: { enumerable: true, get: () => githubReturn },
    providerSignIn: { enumerable: true, get: () => providerSignIn },
    connectKeys: { enumerable: true, get: () => connectKeys },
    orgField: { enumerable: true, get: () => orgField },
    orgAppField: { enumerable: true, get: () => orgAppField },
})
}
