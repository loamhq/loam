import { nodePluginPage } from './plugin-page-fixture.js'
import { formatDate } from '../public/time.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { SHAPES, SEVERAL, REFUSED, expiringIn, EXPIRED, LOGIN_REFUSED } from './fixtures/acme.js'
const hook = nodePluginPage('acme', 'test/fixtures/acme-page')
const { acmeView, acmeNotice, acmeAccounts, ACME_USES } = await import('./fixtures/acme-page/page/view.js')
hook.deregister()
// Acme's card, what it says in each state, and when it needs a person.
//
// `plugin-pages/acme/page/view.js` touches no DOM and holds no clock, so every line the
// card draws is asserted here without a page. The page itself, driven, is
// `test/acme.test.js`.




const NOW = Date.parse('2026-10-07T20:00:00Z')

test('acme view: unconfigured is Not connected, no quiet line, and Sign in', () => {
  const view = acmeView(SHAPES.unconfigured, { now: NOW })
  assert.deepEqual(view.lines, [{ key: 'who', text: 'Not connected' }])
  assert.equal(view.quiet, null)
  assert.equal(view.uses, ACME_USES)
  assert.deepEqual(view.actions, ['signin'])
})

test('acme view: connected names who and on which account, linked, with the good-until date', () => {
  const view = acmeView(SHAPES.connected, { now: NOW })
  assert.deepEqual(view.lines, [
    { key: 'who', text: 'Connected as Sam H on ', account: { text: 'Account 5550002', href: 'https://app.acme.example/5550002' } },
  ])
  assert.deepEqual(view.quiet, { tone: 'quiet', text: `token good until ${formatDate('2027-10-07T00:00:00.000Z')}` })
  assert.deepEqual(view.actions, ['refresh', 'disconnect'])
})

test('acme view: a token with no expiry says so, quietly', () => {
  assert.deepEqual(acmeView(SHAPES.noExpiry, { now: NOW }).quiet, { tone: 'quiet', text: 'no expiry on this token' })
  assert.equal(acmeNotice(SHAPES.noExpiry, { now: NOW }), null)
})

test('acme view: unhealthy carries the host’s sentence in the warning ink, and Sign in again', () => {
  const view = acmeView(SHAPES.unhealthy, { now: NOW })
  assert.deepEqual(view.quiet, { tone: 'warn', text: REFUSED })
  assert.deepEqual(view.actions, ['signin', 'refresh', 'disconnect'])
})

test('acme notice: unhealthy, and within 14 days of the expiry, and not before', () => {
  assert.deepEqual(acmeNotice(SHAPES.unhealthy, { now: NOW }), { text: 'Acme needs a new token' })
  assert.deepEqual(acmeNotice(expiringIn(14, NOW), { now: NOW }), { text: 'Acme needs a new token' })
  assert.deepEqual(acmeNotice(expiringIn(-2, NOW), { now: NOW }), { text: 'Acme needs a new token' })
  assert.equal(acmeNotice(expiringIn(15, NOW), { now: NOW }), null)
  assert.equal(acmeNotice(SHAPES.connected, { now: NOW }), null)
  assert.equal(acmeNotice(SHAPES.unconfigured, { now: NOW }), null)
  assert.equal(acmeNotice(null), null)
  // Near the expiry the date goes to the warning ink and Sign in comes back.
  const near = acmeView(expiringIn(5, NOW), { now: NOW })
  assert.equal(near.quiet.tone, 'warn')
  assert.deepEqual(near.actions, ['signin', 'refresh', 'disconnect'])
})



test('acme accounts: the several-accounts refusal names them; any other refusal names none', () => {
  assert.deepEqual(acmeAccounts(SEVERAL), SEVERAL.accounts)
  assert.equal(acmeAccounts({ error: REFUSED }), null)
  assert.equal(acmeAccounts(null), null)
})

test('acme view login: self-renewal, unhealthy warning, and expired sentence without a notice', () => {
  assert.deepEqual(acmeView(SHAPES.login).quiet, { tone: 'quiet', text: 'signed in · renews itself' })
  assert.equal(acmeNotice(SHAPES.login), null)
  assert.deepEqual(acmeView(SHAPES.loginUnhealthy).quiet, { tone: 'warn', text: LOGIN_REFUSED })
  assert.deepEqual(acmeNotice(SHAPES.loginUnhealthy), { text: 'Acme needs a new sign-in' })
  assert.deepEqual(acmeView(SHAPES.expired).quiet, { tone: 'warn', text: EXPIRED })
  assert.equal(acmeNotice(SHAPES.expired), null)
  assert.equal(acmeNotice(SHAPES.denied), null)
})
