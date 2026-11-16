import test from 'node:test'
import assert from 'node:assert/strict'
import { createProfiles, mountProfile } from '../public/agent-profile.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'

const walk = node => [node, ...(node.childNodes || []).flatMap(walk)]
const selected = node => node.childNodes.find(option => option.hasAttribute('selected'))?.getAttribute('value')
const settle = () => new Promise(resolve => setImmediate(resolve))

for (const project of ['Notebook', 'default']) test(`${project} engineer tiers save individually, reset to model and redraw after provider changes and refusals`, async () => {
  installDom()
  const calls = [], tiers = { quick: null, standard: 'deep', hard: 'retired-model' }
  const row = { project, own: { tiers }, effective: { provider: 'codex', model: 'fast', tiers: {} }, providers: ['codex', 'claude-code'] }
  const resolve = () => {
    row.effective.tiers = Object.fromEntries(Object.entries(tiers).map(([tier, model]) => [tier, { model: model || row.effective.model, from: model ? 'tier' : 'model' }]))
    return structuredClone(row)
  }
  let refuse = false
  const controller = createProfiles({ redraw() {}, api: async (method, path, body) => {
    calls.push({ method, path, body })
    if (method === 'PATCH') {
      if (refuse) throw new Error('This provider does not offer that tier model.')
      if (body.tiers) Object.assign(tiers, body.tiers)
      if (body.provider) { row.effective.provider = body.provider; row.effective.model = 'fable'; for (const tier in tiers) tiers[tier] = null }
      const updated = resolve()
      return { engineers: [updated], default: updated }
    }
    return resolve()
  } })
  const host = { profiles: controller, profileActivity: { view: () => h('div') }, schedule() {}, settingsHead: title => h('h2', { text: title }),
    state: { providers: [{ id: 'codex', connected: true, models: [{ id: 'fast', name: 'Fast' }, { id: 'deep', name: 'Deep' }] },
      { id: 'claude-code', connected: true, models: [{ id: 'fable', name: 'Fable' }] }] } }
  const view = mountProfile(host)
  const nodes = () => walk(h('div', {}, view.settingsDetail()))
  const picker = label => nodes().find(node => node.nodeName === 'SELECT' && node.getAttribute('aria-label') === label)
  await controller.load('engineer', project)
  assert.equal(selected(picker('Quick')), '')
  assert.equal(picker('Quick').firstChild.textContent, 'Default · Fast')
  assert.equal(selected(picker('Standard')), 'deep')
  assert.equal(selected(picker('Hard')), 'retired-model')
  assert.ok(picker('Hard').childNodes.at(-1).hasAttribute('disabled'))
  picker('Quick').fire('change', { target: { value: 'fast' } }); await settle()
  assert.deepEqual(calls.at(-1), { method: 'PATCH', path: `/engineers/${project}`, body: { tiers: { quick: 'fast' } } })
  assert.equal(selected(picker('Quick')), 'fast')
  assert.equal(selected(picker('Standard')), 'deep')
  picker('Standard').fire('change', { target: { value: '' } }); await settle()
  assert.deepEqual(calls.at(-1).body, { tiers: { standard: null } })
  assert.equal(picker('Standard').firstChild.textContent, 'Default · Fast')
  refuse = true
  picker('Quick').fire('change', { target: { value: 'deep' } }); await settle()
  assert.equal(controller.st.error, 'This provider does not offer that tier model.')
  assert.equal(selected(picker('Quick')), 'fast')
  refuse = false
  picker('Provider').fire('change', { target: { value: 'claude-code' } }); await settle()
  for (const label of ['Quick', 'Standard', 'Hard']) {
    assert.equal(selected(picker(label)), '')
    assert.equal(picker(label).firstChild.textContent, 'Default · Fable')
    assert.deepEqual(picker(label).childNodes.map(node => node.getAttribute('value')), ['', 'fable'])
  }
})
