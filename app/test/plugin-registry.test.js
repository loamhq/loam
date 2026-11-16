import test from 'node:test'
import assert from 'node:assert/strict'
import { createCoreRegistry } from '../public/core-registrations.js'

test('plugins and core share removable places, commands, events and declared whole-component overrides', () => {
  const registry = createCoreRegistry(), plugin = registry.scope({ id: 'proof', overrides: ['core.message'] })
  const registration = plugin.places.rail.section.add({ id: 'proof', title: 'Proof', render: () => 'a place' })
  const command = plugin.commands.register({ id: 'proof.open', run: context => context.id })
  let count = 0
  plugin.events.on('message.arrived', () => count++)
  plugin.places.message.renderer.add({ id: 'proof.message', component: 'core.message', render: context => `Replaced ${context.args[0]}` })
  assert.equal(registry.component('core.message', { args: ['answer'], defaultView: () => 'default' }), 'Replaced answer')
  assert.equal(plugin.commands.run('proof.open', { id: 'example' }), 'example')
  registry.emit('message.arrived', {}); assert.equal(count, 1)
  registration.remove(); registration.remove(); assert.equal(registry.entries('rail.section').length, 0)
  command.remove(); assert.throws(() => plugin.commands.run('proof.open'), /Unknown command/)
  plugin.dispose(); registry.emit('message.arrived', {}); assert.equal(count, 1)
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
})

test('a broken renderer releases all its registrations and restores core without stopping another plugin', () => {
  const failures = [], registry = createCoreRegistry({ failed: (id, error) => failures.push([id, error.message]) })
  const broken = registry.scope({ id: 'broken', overrides: ['core.message'] }), good = registry.scope({ id: 'good' })
  broken.places.message.renderer.add({ id: 'bad.message', component: 'core.message', render: () => { throw new Error('A broken renderer') } })
  broken.places.app.overlay.add({ id: 'bad.overlay', render: () => 'bad overlay' })
  good.places.app.overlay.add({ id: 'good.overlay', render: () => 'good overlay' })
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
  assert.deepEqual(failures, [['broken', 'A broken renderer']])
  assert.deepEqual(registry.draw('app.overlay', { slot: 'overlay' }), ['good overlay'])
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'default')
})

test('reserved places stay reserved and undeclared component replacements are allowed, and footer transforms compose', () => {
  const registry = createCoreRegistry(), plugin = registry.scope({ id: 'example' })
  assert.equal(plugin.places.thread.kind, undefined)
  plugin.places.message.renderer.add({ id: 'custom', component: 'core.message', render: () => 'custom' })
  assert.equal(registry.component('core.message', { defaultView: () => 'default' }), 'custom')
  plugin.places.message.footer.action.add({ id: 'extra', slot: 'row', order: 10, when: c => c.slot === 'row', render: c => [...c.view, 'extra'] })
  assert.deepEqual(registry.reduce('message.footer.action', { slot: 'row', defaultView: () => ['base'] }, []), ['base', 'extra'])
})
