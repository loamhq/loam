import { formatDate } from '../public/time.js'
// GitHub's card, what it says in each state, and the two ways back from GitHub.
//
// `public/integrations.js` touches no DOM and holds no clock, so every line the
// card draws and every body the return paths hand the broker is asserted here
// without a page. The page itself, driven, is `test/github.test.js` and
// `test/github-register.test.js`.

import test from 'node:test'
import assert from 'node:assert/strict'

import {
  githubView,
  githubNotice,
  returnFrom,
  manifestForm,
  manifestTarget,
  orgView,
  orgApps,
  offersOrgs,
  orgLogin,
  installOrg,
  orgManifestTarget,
  GITHUB_USES,
} from '../public/integrations.js'

import { SHAPES, INSTALLED, USER, ORG_SHAPES, ORG_USER, withOrgs } from './fixtures/github.js'

const NOW = Date.parse('2026-09-30T22:00:00Z')
const texts = (view) => view.lines.map((l) => l.text)

test('unregistered: Not set up, and the one pill is Set up on GitHub', () => {
  const view = githubView(SHAPES.unregistered, { now: NOW })
  assert.deepEqual(texts(view), ['Not set up'])
  assert.equal(view.quiet, null)
  assert.equal(view.uses, GITHUB_USES)
  assert.deepEqual(view.actions, ['setup'])
})

test('registered: the slug, linked to the app on GitHub, and Install', () => {
  const view = githubView(SHAPES.registered, { now: NOW })
  assert.deepEqual(view.lines, [{ key: 'app', text: 'Registered as loam', href: 'https://github.com/apps/loam' }])
  assert.deepEqual(view.actions, ['install'])
})

test('installed with no user: the account and its repositories, and Reconnect', () => {
  const view = githubView(SHAPES.installed, { now: NOW })
  assert.deepEqual(texts(view), ['Registered as loam', 'Installed on octo-owner, all repositories'])
  assert.deepEqual(view.actions, ['reconnect'])
})

test('a selection of repositories is counted when the host counts it, and not invented when it does not', () => {
  const pick = (over) => texts(githubView({ ...SHAPES.installed, installed: { ...INSTALLED, repository_selection: 'selected', ...over } }, { now: NOW }))[1]
  assert.equal(pick({ repository_count: 3 }), 'Installed on octo-owner, 3 repositories')
  assert.equal(pick({ repositories: ['a'] }), 'Installed on octo-owner, 1 repository')
  assert.equal(pick({}), 'Installed on octo-owner, selected repositories')
})

test('authorized: signed in, the six-month date quietly, Refresh and Disconnect', () => {
  const view = githubView(SHAPES.authorized, { now: NOW })
  assert.deepEqual(texts(view), ['Registered as loam', 'Installed on octo-owner, all repositories', 'Signed in as octo-owner'])
  assert.deepEqual(view.quiet, { tone: 'quiet', text: `token renews itself · authorization good until ${formatDate('2027-04-02T21:37:31Z')}` })
  assert.deepEqual(view.actions, ['refresh', 'disconnect'])
  assert.equal(githubNotice(SHAPES.authorized, { now: NOW }), null)
})

test('unhealthy: the host’s error in the warning ink instead of the date, Reconnect first, and a notice', () => {
  const view = githubView(SHAPES.unhealthy, { now: NOW })
  assert.deepEqual(view.quiet, { tone: 'warn', text: SHAPES.unhealthy.health.last_error })
  assert.deepEqual(view.actions, ['reconnect', 'refresh', 'disconnect'])
  assert.deepEqual(githubNotice(SHAPES.unhealthy, { now: NOW }), { text: 'GitHub needs reconnecting' })
})

test('the refresh token within fourteen days of its end is a notice, and fifteen days out is not', () => {
  const at = (days) => ({ ...SHAPES.authorized, user: { ...USER, refresh_expires_at: new Date(NOW + days * 86400000).toISOString() } })
  assert.equal(githubNotice(at(15), { now: NOW }), null)
  assert.deepEqual(githubNotice(at(14), { now: NOW }), { text: 'GitHub needs reconnecting' })
  assert.deepEqual(githubNotice(at(-1), { now: NOW }), { text: 'GitHub needs reconnecting' })
  const soon = githubView(at(10), { now: NOW })
  assert.equal(soon.quiet.tone, 'warn')
  assert.deepEqual(soon.actions, ['reconnect', 'refresh', 'disconnect'])
})

test('no answer is no card, and says nothing in the notice row', () => {
  assert.equal(githubView(null), null)
  assert.equal(githubNotice(null), null)
})



test('the two return paths hand their query to the right broker route, whole', () => {
  assert.deepEqual(returnFrom('/integrations/github/registered', '?code=abc&state=s1'), { route: 'register', body: { code: 'abc', state: 's1' } })
  assert.deepEqual(returnFrom('/integrations/github/installed', '?code=c2&installation_id=99&setup_action=install&state=s2'), {
    route: 'callback',
    body: { code: 'c2', installation_id: 99, setup_action: 'install', state: 's2' },
  })
  // A field GitHub did not send is null, for the broker to name.
  assert.deepEqual(returnFrom('/integrations/github/registered', ''), { route: 'register', body: { code: null, state: null } })
  assert.equal(returnFrom('/', '?code=abc&state=s1'), null)
  assert.equal(returnFrom('/integrations/github/other', '?code=abc'), null)
})

// A fake element with nothing but what the form is built from.
function make(tag) {
  const attrs = new Map()
  const children = []
  return {
    tag,
    attrs,
    children,
    setAttribute: (k, v) => attrs.set(k, String(v)),
    appendChild: (c) => children.push(c),
  }
}

test('the manifest form posts the manifest as a hidden field to GitHub, and is hidden by an attribute', () => {
  const manifest = JSON.stringify({ name: 'Loam', url: 'https://example.invalid' })
  const form = manifestForm(make, { manifest, post_url: 'https://github.com/settings/apps/new?state=s1', state: 's1' })
  assert.equal(form.tag, 'form')
  assert.deepEqual(Object.fromEntries(form.attrs), { method: 'post', action: 'https://github.com/settings/apps/new?state=s1', hidden: '' })
  assert.equal(form.children.length, 1)
  assert.deepEqual(Object.fromEntries(form.children[0].attrs), { type: 'hidden', name: 'manifest', value: manifest })
  // Never a style: the page's policy refuses one.
  assert.equal(form.attrs.has('style'), false)
  // An object is sent as the JSON string GitHub reads.
  const again = manifestForm(make, { manifest: { name: 'Loam' }, post_url: 'https://github.com/settings/apps/new' })
  assert.equal(again.children[0].attrs.get('value'), '{"name":"Loam"}')
})

test('a manifest target that is not GitHub over https is no form at all', () => {
  assert.equal(manifestTarget('http://github.com/settings/apps/new'), null)
  assert.equal(manifestTarget('https://github.com.example.invalid/x'), null)
  assert.equal(manifestTarget('not a url'), null)
  assert.equal(manifestForm(make, { manifest: '{}', post_url: 'https://example.invalid/' }), null)
})

// ------------------------------------------------------ apps an org owns

test('a broker from before organizations’ apps offers no Connect, and one from after does, with none yet', () => {
  assert.equal(offersOrgs(SHAPES.authorized), false)
  assert.equal(offersOrgs(withOrgs(SHAPES.authorized)), true)
  assert.deepEqual(orgApps(SHAPES.authorized), [])
  assert.deepEqual(orgApps(withOrgs(SHAPES.unregistered, ORG_SHAPES.registered)).map((o) => o.org), ['octo-org'])
})

test('an org app created and not installed: its app, linked, and Install', () => {
  const view = orgView(ORG_SHAPES.registered, { now: NOW })
  assert.equal(view.title, 'GitHub · octo-org')
  assert.equal(view.word, 'Created, not installed yet')
  assert.deepEqual(view.lines, [{ key: 'app', text: 'Registered as loam-for-octo-org', href: 'https://github.com/apps/loam-for-octo-org' }])
  assert.equal(view.quiet, null)
  assert.deepEqual(view.actions, ['install'])
})

test('an org app installed on selected repositories with nobody authorized: Reconnect', () => {
  const view = orgView(ORG_SHAPES.installed, { now: NOW })
  assert.deepEqual(texts(view), ['Registered as loam-for-octo-org', 'Installed on octo-org, selected repositories'])
  assert.equal(view.word, 'Installed, not authorized yet')
  assert.deepEqual(view.actions, ['reconnect'])
})

test('an org app authorized: who authorized it, the quiet date, and no pills', () => {
  const view = orgView(ORG_SHAPES.authorized, { now: NOW })
  assert.deepEqual(texts(view), ['Registered as loam-for-octo-org', 'Installed on octo-org, selected repositories', 'Authorized by octo-owner'])
  assert.equal(view.word, 'Connected')
  assert.deepEqual(view.quiet, { tone: 'quiet', text: `token renews itself · authorization good until ${formatDate('2027-04-02T21:37:31Z')}` })
  assert.deepEqual(view.actions, [])
})

test('an org app unhealthy: its own error, Reconnect, and a notice naming the org — the first app untouched', () => {
  const view = orgView(ORG_SHAPES.unhealthy, { now: NOW })
  assert.equal(view.word, 'Needs reconnecting')
  assert.deepEqual(view.quiet, { tone: 'warn', text: ORG_SHAPES.unhealthy.health.last_error })
  assert.deepEqual(view.actions, ['reconnect'])
  const gh = withOrgs(SHAPES.authorized, ORG_SHAPES.unhealthy)
  assert.deepEqual(githubNotice(gh, { now: NOW }), { text: 'GitHub for octo-org needs reconnecting' })
  assert.deepEqual(githubView(gh, { now: NOW }).actions, ['refresh', 'disconnect'])
  // The first app's own warning is the first app's words.
  assert.deepEqual(githubNotice(withOrgs(SHAPES.unhealthy, ORG_SHAPES.unhealthy), { now: NOW }), { text: 'GitHub needs reconnecting' })
  // An org authorization within two weeks of its end is a notice too.
  const soon = { ...ORG_SHAPES.authorized, user: { ...ORG_USER, refresh_expires_at: new Date(NOW + 10 * 86400000).toISOString() } }
  assert.deepEqual(orgView(soon, { now: NOW }).actions, ['reconnect'])
  assert.deepEqual(githubNotice(withOrgs(SHAPES.authorized, soon), { now: NOW }), { text: 'GitHub for octo-org needs reconnecting' })
})

test('an organization’s login is GitHub’s rule for one, as the host checks it', () => {
  assert.equal(orgLogin('  octo-org '), 'octo-org')
  assert.equal(orgLogin('a'), 'a')
  assert.equal(orgLogin('-octo'), null)
  assert.equal(orgLogin('octo-'), null)
  assert.equal(orgLogin('octo org'), null)
  assert.equal(orgLogin('https://github.com/octo-org'), null)
  assert.equal(orgLogin('x'.repeat(40)), null)
  assert.equal(orgLogin(''), null)
})

test('an install address names its org app by slug, and the first app’s names none', () => {
  const gh = withOrgs(SHAPES.authorized, ORG_SHAPES.registered)
  assert.equal(installOrg('https://github.com/apps/loam-for-octo-org/installations/new?state=r1', gh), 'octo-org')
  assert.equal(installOrg('https://github.com/apps/loam/installations/new?state=r1', gh), null)
  assert.equal(installOrg(null, gh), null)
  assert.equal(installOrg('not a url', gh), null)
})

test('an org’s manifest goes to that organization’s create page, and a personal one is refused', () => {
  const org = 'https://github.com/organizations/octo-org/settings/apps/new?state=m2'
  assert.equal(orgManifestTarget(org, 'octo-org'), org)
  assert.equal(orgManifestTarget(org, 'Octo-Org'), org)
  // What a broker from before would answer: the first app's page. Creating
  // that app would replace the first one, so no form.
  assert.equal(orgManifestTarget('https://github.com/settings/apps/new?state=m2', 'octo-org'), null)
  assert.equal(orgManifestTarget('https://github.com/organizations/other/settings/apps/new', 'octo-org'), null)
  assert.equal(manifestForm(make, { manifest: '{}', post_url: 'https://github.com/settings/apps/new' }, { org: 'octo-org' }), null)
  const form = manifestForm(make, { manifest: '{}', post_url: org }, { org: 'octo-org' })
  assert.equal(form.attrs.get('action'), org)
})
