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
const ada = { source: 'github', login: 'ada', name: 'Ada Lovelace', email: '1815+ada@users.noreply.github.com' }
const byDefault = { identity: null, effective: ada, status: 'default', default: 'github', prompt: null }
const missing = { identity: null, effective: null, status: 'missing', default: 'missing', prompt: 'Your agents’ commits carry the engineer’s name because no GitHub account is connected.' }
const chosen = { identity: ada, effective: ada, status: 'set', prompt: null }
const neutral = { identity: 'neutral', effective: null, status: 'neutral', prompt: null }
const on = view => all(view, 'on').map(n => n.textContent)

test('default with GitHub connected: the name, where it came from, Default on, no prompt', async () => {
  const f = setup(byDefault)
  await f.ui.load()
  assert.deepEqual(f.asked, [['GET', '/settings/git']])
  const v = f.view({ github: true })
  assert.match(text(v), /Ada Lovelace · 1815\+ada@users\.noreply\.github\.com/)
  assert.match(text(v), /Default: your connected GitHub account \(ada\)/)
  assert.deepEqual(on(v), ['Default'])
  assert.match(text(v), /Also open pull requests as me/)
  assert.doesNotMatch(text(v), /no GitHub account is connected/)
})

test('nothing connected: the engineer’s name and the prompt; no pull-request box', async () => {
  const f = setup(missing)
  await f.ui.load()
  const v = f.view({ github: false })
  assert.match(text(v), /<project> engineer/)
  assert.match(text(v), /no GitHub account is connected/)
  assert.doesNotMatch(text(v), /Also open pull requests as me/)
})

test('neutral and back: each is one PATCH on the route a persona uses', async () => {
  const f = setup(byDefault)
  await f.ui.load()
  f.answer(neutral)
  await f.ui.save('neutral', 'n')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: 'neutral' }])
  const v = f.view({ github: true })
  assert.deepEqual(on(v), ['Neutral'])
  assert.match(text(v), /Neutral, by your choice/)
  assert.doesNotMatch(text(v), /Also open pull requests as me/)
  f.answer(byDefault)
  await f.ui.save(null, 'd')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: null }])
})

test('the explicit GitHub choice is the pull-request box, and Default stays lit', async () => {
  const f = setup(byDefault)
  await f.ui.load()
  f.answer(chosen)
  await f.ui.save('github', 'set')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: 'github' }])
  const v = f.view({ github: true })
  assert.deepEqual(on(v), ['Default'])
  const box = all(v, 'git-identity-asme-box')[0]
  assert.ok(box && box.getAttribute('checked') !== null)
  f.answer(byDefault)
  await f.ui.save(null, 'd')
  assert.equal(all(f.view({ github: true }), 'git-identity-asme-box')[0].getAttribute('checked'), null)
})

test('typed: name and email in one PATCH; a refusal is shown, nothing else changes', async () => {
  const f = setup(missing)
  await f.ui.load()
  f.answer(new Error('noreply@anthropic.com is not a person’s address'))
  await f.ui.save({ name: 'Ada', email: 'noreply@anthropic.com' }, 'Saved.')
  assert.deepEqual(f.asked.at(-1), ['PATCH', '/settings/git', { identity: { name: 'Ada', email: 'noreply@anthropic.com' } }])
  assert.match(text(f.view()), /not a person’s address/)
  assert.match(text(f.view()), /<project> engineer/)
})
