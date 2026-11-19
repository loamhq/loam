import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all, text } from './browser.js'
import { h } from '../public/ui.js'
import { createGitIdentity } from '../public/git-identity.js'

function setup(first) {
  installDom()
  const asked = []
  let answer = first
  const ui = createGitIdentity({ changed() {}, api: async (...args) => { asked.push(args); if (answer instanceof Error) throw answer; return answer } })
  return { asked, ui, answer: value => { answer = value }, view: (opts) => h('div', {}, ui.view(opts)) }
}
const unset = { identity: null, status: 'missing', prompt: 'No name is set' }
const fromGithub = { identity: { source: 'github', login: 'ada', name: 'Ada Lovelace', email: '1815+ada@users.noreply.github.com' }, status: 'set', prompt: null }

test('unset: says so, and offers GitHub only when an account is connected', async () => {
  const f = setup(unset)
  await f.ui.load()
  assert.deepEqual(f.asked, [['GET', '/settings/git']])
  assert.match(text(f.view({ github: true })), /Not set\. New commits by your agents carry the engineer’s name/)
  assert.ok(all(f.view({ github: true }), 'pillbtn').some(n => n.textContent === 'Use my GitHub account'))
  assert.ok(!all(f.view({ github: false }), 'pillbtn').some(n => n.textContent === 'Use my GitHub account'))
})

test('choosing GitHub sends the one route a persona uses, and shows the name and no-reply', async () => {
  const f = setup(unset)
  await f.ui.load()
  f.answer(fromGithub)
  await f.ui.save('github', 'set')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: 'github' }])
  assert.match(text(f.view({ github: true })), /Ada Lovelace · 1815\+ada@users\.noreply\.github\.com · from GitHub/)
})

test('typed: name and email in one PATCH; a refusal is shown, nothing else changes', async () => {
  const f = setup(unset)
  await f.ui.load()
  f.answer(new Error('noreply@anthropic.com is not a person’s address'))
  await f.ui.save({ name: 'Ada', email: 'noreply@anthropic.com' }, 'Saved.')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: { name: 'Ada', email: 'noreply@anthropic.com' } }])
  assert.match(text(f.view()), /not a person’s address/)
  assert.match(text(f.view()), /Not set/)
  f.answer({ identity: null, status: 'missing' })
  await f.ui.save(null, 'Cleared.')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: null }])
})
