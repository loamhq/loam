import { formatDate } from './time.js'
// The integrations, and what GitHub's state says in plain words.
//
// An integration is an account on a service the machine's own work reaches —
// not a provider, which is a vendor account a chat runs on. GitHub is the first
// integration: the host holds the app it registered on GitHub, the
// installation on the person's account and their user authorization, and uses them
// for new project repos and the engineers' pushes. This page shows that state
// and drives the flow that changes it; it holds no token and decides nothing.
//
// It touches no DOM and holds no clock of its own: `now` is passed in, so what
// the card and the notice say can be asserted in `node --test`.
//
// **Everything here is read off `GET /v1/integrations/github`**, whose `status`
// is one of `unregistered`, `registered`, `installed`, `authorized` and
// `unhealthy`. The card shows what the state has, in the order the state gains
// it, so a connected card still says what it is registered as and where it is
// installed.

// The refresh token lasts six months and renewing it takes a person. The notice
// starts two weeks out: early, for a thing only a person can fix.
export const GITHUB_WARN_DAYS = 14

// The one line of what Loam uses it for. The host sends `uses` as its own two
// words; this is them, in a sentence.
export const GITHUB_USES = 'Loam uses it for new project repos and the engineers’ pushes.'

const DAY = 86400000

// The card, as a list of what to draw. `null` for no reading at all.
//
// - `lines`: the state's own facts, in order — each `{ text, href? }`.
// - `quiet`: the one line under them — the authorization's good-until date, or
//   the host's health error in the warning ink — or `null`.
// - `actions`: which pills apply, in order, by id: `setup`, `install`,
//   `reconnect`, `refresh`, `disconnect`.
export function githubView(gh, { now = Date.now() } = {}) {
  if (!gh || typeof gh !== 'object') return null
  const status = typeof gh.status === 'string' ? gh.status : 'unregistered'
  const app = gh.app && typeof gh.app === 'object' ? gh.app : null
  const installed = gh.installed && typeof gh.installed === 'object' ? gh.installed : null
  const user = gh.user && typeof gh.user === 'object' ? gh.user : null
  const error = typeof gh.health?.last_error === 'string' && gh.health.last_error !== '' ? gh.health.last_error : null

  const lines = []
  if (!app) lines.push({ key: 'setup', text: 'Not set up' })
  else lines.push({ key: 'app', text: `Registered as ${app.slug ?? 'an app'}`, href: typeof app.url === 'string' ? app.url : null })
  if (installed) lines.push({ key: 'installed', text: `Installed on ${installed.account ?? 'an account'}, ${reposWords(installed)}` })
  if (user) lines.push({ key: 'user', text: `Signed in as ${user.login ?? 'someone'}` })

  const connected = status === 'authorized' || status === 'unhealthy'
  const until = user ? dateWords(user.refresh_expires_at) : null
  const quiet = error
    ? { tone: 'warn', text: error }
    : until
      ? { tone: refreshSoon(user, now) ? 'warn' : 'quiet', text: `token renews itself · authorization good until ${until}` }
      : null

  const actions = []
  if (!app) actions.push('setup')
  else if (!installed) actions.push('install')
  else if (!user || status === 'unhealthy' || refreshSoon(user, now)) actions.push('reconnect')
  if (connected) actions.push('refresh', 'disconnect')

  return { status, connected, lines, quiet, uses: GITHUB_USES, actions }
}

// Whether GitHub needs a person: the installation gone or the authorization
// failing — the host's `unhealthy` — or the refresh token within two weeks of
// running out, on the first app or on any organization's. The words are the
// notice row's, and "Settings" beside them is the page's link.
export function githubNotice(gh, { now = Date.now() } = {}) {
  if (!gh || typeof gh !== 'object') return null
  if (gh.status === 'unhealthy' || refreshSoon(gh.user, now)) return { text: 'GitHub needs reconnecting' }
  const org = orgApps(gh).find((o) => o.status === 'unhealthy' || refreshSoon(o.user, now))
  if (org) return { text: `GitHub for ${org.org} needs reconnecting` }
  return null
}

// ------------------------------------------------------ apps an org owns
//
// Beside the first app the host can hold one app per GitHub organization, owned
// by that organization and answering for its repositories alone (2026-10-06).
// `org_apps` on the state is a list of `{org, status, app, installed, user,
// health}`, each with its own health: one org's app failing never makes the
// first app's card unhealthy, and the reverse. A host from before then sends
// no `org_apps` key at all, which is how the page knows not to offer
// connecting one — see `offersOrgs()`.

// GitHub's rule for an organization's login, as the host checks it: letters,
// digits and single hyphens inside, at most 39.
const ORG_LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/

export function orgLogin(text) {
  const org = String(text ?? '').trim()
  return ORG_LOGIN.test(org) ? org : null
}

export function orgApps(gh) {
  return Array.isArray(gh?.org_apps) ? gh.org_apps.filter((o) => o && typeof o === 'object' && typeof o.org === 'string') : []
}

// Whether this host holds apps for organizations at all. A host from before
// would answer the manifest route with the first app's manifest and ignore the
// `org` — and creating that would replace the first app — so the action is not
// drawn against one.
export function offersOrgs(gh) {
  return Array.isArray(gh?.org_apps)
}

const ORG_STATUS = {
  registered: 'Created, not installed yet',
  installed: 'Installed, not authorized yet',
  authorized: 'Connected',
  unhealthy: 'Needs reconnecting',
}

// One organization's card, as a list of what to draw — the first app's shape,
// with its own `title` and `word` for the status. Its one pill is Install
// until it is installed and Reconnect when something about it needs a person;
// Refresh and Disconnect stay on the first app's card, because the host's
// refresh checks every app and its forget forgets every app.
export function orgView(own, { now = Date.now() } = {}) {
  if (!own || typeof own !== 'object' || typeof own.org !== 'string') return null
  const status = typeof own.status === 'string' ? own.status : 'registered'
  const app = own.app && typeof own.app === 'object' && own.app.slug ? own.app : null
  const installed = own.installed && typeof own.installed === 'object' ? own.installed : null
  const user = own.user && typeof own.user === 'object' ? own.user : null
  const error = typeof own.health?.last_error === 'string' && own.health.last_error !== '' ? own.health.last_error : null

  const lines = []
  if (app) lines.push({ key: 'app', text: `Registered as ${app.slug}`, href: typeof app.url === 'string' ? app.url : null })
  if (installed) lines.push({ key: 'installed', text: `Installed on ${installed.account ?? own.org}, ${reposWords(installed)}` })
  if (user) lines.push({ key: 'user', text: `Authorized by ${user.login ?? 'someone'}` })

  const until = user ? dateWords(user.refresh_expires_at) : null
  const quiet = error
    ? { tone: 'warn', text: error }
    : until
      ? { tone: refreshSoon(user, now) ? 'warn' : 'quiet', text: `token renews itself · authorization good until ${until}` }
      : null

  const actions = []
  if (!installed && status !== 'unhealthy') actions.push('install')
  else if (!user || status === 'unhealthy' || refreshSoon(user, now)) actions.push('reconnect')

  return { org: own.org, status, word: ORG_STATUS[status] ?? status, title: `GitHub · ${own.org}`, lines, quiet, actions }
}

// Which organization an install address belongs to, if any: the address is
// GitHub's `/apps/<slug>/installations/new`, and the slug is the org app's. A
// register answer says nothing else about which app it registered, and this
// is how the org's Install offers that answer's address and not the first
// app's.
export function installOrg(url, gh) {
  let slug
  try {
    slug = /^\/apps\/([^/]+)\/installations\/new\/?$/.exec(new URL(String(url ?? '')).pathname)?.[1]
  } catch {
    return null
  }
  if (!slug) return null
  return orgApps(gh).find((o) => o.app?.slug === decodeURIComponent(slug))?.org ?? null
}

// The org's manifest goes to that organization's own create page and nowhere
// else. A host that ignored the `org` would name the personal create page, and
// the app made there would replace the first one — so anything else is
// refused here, before a form is built.
export function orgManifestTarget(postUrl, org) {
  const action = manifestTarget(postUrl)
  if (!action || typeof org !== 'string') return null
  const path = new URL(action).pathname.toLowerCase()
  return path === `/organizations/${org.toLowerCase()}/settings/apps/new` ? action : null
}

function refreshSoon(user, now) {
  const t = Date.parse(user?.refresh_expires_at ?? '')
  if (Number.isNaN(t)) return false
  return t - now <= GITHUB_WARN_DAYS * DAY
}

// "all repositories", or how many were chosen. The state names the selection;
// a count is read if the host sends one, and "selected repositories" said if
// it does not, rather than a number made up.
function reposWords(installed) {
  if (installed.repository_selection === 'all') return 'all repositories'
  const n = Array.isArray(installed.repositories)
    ? installed.repositories.length
    : typeof installed.repository_count === 'number'
      ? installed.repository_count
      : null
  if (n === null) return 'selected repositories'
  return n === 1 ? '1 repository' : `${n} repositories`
}

function dateWords(iso) {
  return formatDate(iso) || null
}

// ------------------------------------------------------ coming back from GitHub
//
// GitHub sends the browser back to the app, never to the broker, at one of two
// paths the manifest the broker builds names. Each carries a query this page
// hands to the broker whole, and each is a different route there.
export const RETURN_PATHS = {
  '/integrations/github/registered': { route: 'register', fields: ['code', 'state'] },
  '/integrations/github/installed': { route: 'callback', fields: ['code', 'installation_id', 'setup_action', 'state'] },
}

// Which return this is, and the body the broker's route takes — or `null` for
// any other path. A field GitHub did not send is `null` in the body, and the
// broker says what is wrong with it; `installation_id` goes as a number when
// it reads as one, because the broker holds it as one.
export function returnFrom(pathname, search) {
  const spec = RETURN_PATHS[String(pathname ?? '').replace(/\/+$/, '')]
  if (!spec) return null
  const params = new URLSearchParams(search ?? '')
  const body = {}
  for (const field of spec.fields) {
    const value = params.get(field)
    body[field] = value === null || value === '' ? null : value
  }
  if (body.installation_id !== undefined && body.installation_id !== null && /^\d+$/.test(body.installation_id)) {
    body.installation_id = Number(body.installation_id)
  }
  return { route: spec.route, body }
}

// ----------------------------------------------------------------- the manifest
//
// GitHub's manifest flow is a form post from the person's browser: the
// manifest, a JSON string, in a field named `manifest`, posted to the URL the
// broker names — which carries the `state`. The browser lands on GitHub's own
// create page prefilled, and the person clicks Create there. Nothing is pasted.
//
// The target must be GitHub's, over https: this page's policy allows a form to
// post there and nowhere else, so any other URL would be refused by the browser
// without a word, and this says it in one instead.
export function manifestTarget(postUrl) {
  let url
  try {
    url = new URL(String(postUrl ?? ''))
  } catch {
    return null
  }
  return url.protocol === 'https:' && url.hostname === 'github.com' ? url.href : null
}

// The form, built by the caller's `make(tag)` so it is a real element in a
// browser and a fake one in a test. It is `hidden` — an attribute, never a
// style, which this page's policy would refuse. With `org`, the target must be
// that organization's create page (`orgManifestTarget()`).
export function manifestForm(make, { manifest, post_url: postUrl }, { org = null } = {}) {
  const action = org === null ? manifestTarget(postUrl) : orgManifestTarget(postUrl, org)
  if (!action) return null
  const form = make('form')
  form.setAttribute('method', 'post')
  form.setAttribute('action', action)
  form.setAttribute('hidden', '')
  const field = make('input')
  field.setAttribute('type', 'hidden')
  field.setAttribute('name', 'manifest')
  field.setAttribute('value', typeof manifest === 'string' ? manifest : JSON.stringify(manifest ?? {}))
  form.appendChild(field)
  return form
}
