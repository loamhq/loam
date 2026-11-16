import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'
import { createGithubAccounts } from '../public/github-accounts.js'
import { integrationCatalog } from '../public/integration-catalog.js'
import { githubAccounts as accounts } from './plugin-runtime-fixture.mjs'
function setup() {
  installDom()
  const gh = { accounts: structuredClone(accounts) }, asked = [], navigated = [], submitted = []
  let answer
  const ui = createGithubAccounts({ state: () => gh, changed() {}, reload() {}, navigate: url => navigated.push(url), submit: form => submitted.push(form),
    api: async (...args) => { asked.push(args); if (answer instanceof Error) throw answer; return answer } })
  return { gh, asked, ui, navigated, submitted, answer: value => { answer = value }, view: () => h('div', {}, ui.view()) }
}
test('every account has scoped controls; personal disconnect names dependent installations and keeps independent orgs', async () => {
  const f = setup(), view = f.view()
  assert.equal(all(view, 'github-account-row').length, 3)
  assert.match(text(view), /Installation · Installed/)
  assert.match(text(view), /GitHub says this installation has been removed/)
  all(view, 'pillbtn').find(n => n.getAttribute('aria-label') === 'Disconnect octo-owner').fire('click')
  assert.deepEqual(f.asked, [])
  assert.match(text(f.view()), /Also disconnects: octo-team/)
  assert.doesNotMatch(text(all(f.view(), 'github-account-confirm')[0]), /octo-org/)
  f.answer({ accounts: [accounts[2]], account: 'octo-owner', forgot: ['octo-owner', 'octo-team'], uninstall_url: 'https://github.com/settings/installations/42', revoke_url: 'https://github.com/settings/connections/applications/example' })
  await f.ui.disconnect(accounts[0])
  assert.deepEqual(f.asked, [['DELETE', '/integrations/github/accounts/octo-owner']])
  assert.deepEqual(f.gh.accounts, [accounts[2]])
  assert.match(text(f.view()), /Uninstall on GitHubRevoke access on GitHub/)
  assert.deepEqual(f.navigated, [])
})
test('refresh applies the returned list, errors remain scoped, and encoded logins have one route segment', async () => {
  const f = setup()
  f.answer({ accounts: [accounts[1]] })
  await f.ui.refresh({ ...accounts[1], account: 'a/b ?' })
  assert.deepEqual(f.asked[0], ['POST', '/integrations/github/accounts/a%2Fb%20%3F/refresh', {}])
  assert.deepEqual(f.gh.accounts, [accounts[1]])
  f.answer(new Error("no GitHub account 'octo-team' is connected here"))
  await f.ui.refresh(accounts[1])
  assert.match(text(f.view()), /no GitHub account 'octo-team' is connected here/)
  assert.equal(all(f.view(), 'pillbtn').find(n => text(n) === 'Refresh').hasAttribute('disabled'), false)
})
test('add routes install and registration through the existing GitHub flows, including an org-owned app', async () => {
  const f = setup()
  f.answer({ step: 'install', url: 'https://github.com/apps/example/installations/new?state=one' })
  await f.ui.add()
  assert.deepEqual(f.asked[0], ['POST', '/integrations/github/accounts', {}])
  assert.equal(f.navigated[0], 'https://github.com/apps/example/installations/new?state=one')
  for (const options of [{}, { org: 'octo-org', own_app: true }]) {
    const post_url = options.org ? 'https://github.com/organizations/octo-org/settings/apps/new?state=two' : 'https://github.com/settings/apps/new?state=two'
    f.answer({ step: 'register', manifest: { name: 'Example' }, post_url })
    await f.ui.add(options)
    assert.equal(f.submitted.at(-1).getAttribute('action'), post_url)
    assert.equal(f.submitted.at(-1).childNodes[0].getAttribute('value'), '{"name":"Example"}')
  }
  f.answer({ step: 'install', url: 'https://example.org/sign-in' }); await f.ui.add()
  assert.match(text(f.view()), /did not provide a sign-in address/)
  assert.equal(f.navigated.length, 1)
})
test('integration counts include installations and organizations in the host order', () => {
  const list = integrationCatalog({ github: { accounts, loaded: true } })
  assert.equal(list[0].summary, '3 accounts')
  assert.deepEqual(list[0].accounts.map(a => a.name), accounts.map(a => a.account))
})

test('disconnecting an installation or org targets that login and retains the other accounts', async () => {
  for (const account of accounts.slice(1)) {
    const f = setup(), remaining = accounts.filter(row => row.account !== account.account)
    f.answer({ account: account.account, accounts: remaining })
    await f.ui.disconnect(account)
    assert.deepEqual(f.asked, [['DELETE', `/integrations/github/accounts/${account.account}`]])
    assert.deepEqual(f.gh.accounts, remaining)
    assert.equal(f.gh.accounts[0].kind, 'personal')
  }
})
