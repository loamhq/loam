import test from 'node:test'
import assert from 'node:assert/strict'
import { createCoreRegistry } from '../public/core-registrations.js'
import { createPluginLoader } from '../public/plugin-loader.js'
import { activate } from '../../plugins/_listed/read-aloud/page/main.js'

test('panel dictation resets and disable keep captures isolated without publishing audio state to core', async t => {
  const { activate: dictate } = await import('../../plugins/_listed/dictation/page/main.js')
  const waiting = [], services = new Map(), listeners = new Set()
  let stopped = 0
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise(resolve => waiting.push(resolve)),
    addEventListener: (name, callback) => listeners.add(callback),
    removeEventListener: (name, callback) => listeners.delete(callback),
  } } })
  t.after(() => { if (previousNavigator) Object.defineProperty(globalThis, 'navigator', previousNavigator); else delete globalThis.navigator })
  const owner = panelId => ({ panelId, state: {}, audioSessionType() {}, schedule() {}, activeThread: () => null,
    draft: { value: `${panelId} draft`, isConnected: false }, fileField: {} })
  const main = owner('main'), side = owner('side')
  main.panelHosts = () => [main, side]
  const registry = createCoreRegistry(), loader = createPluginLoader({ registry, request: async () => ({}), ui: { host: main } })
  const scope = loader.scope({ id: 'dictation' }), extend = scope.ui.extension
  scope.ui.extension = options => {
    Object.assign(options.helpers, { readField: () => ({ caret: 5 }), caretPoint: () => null })
    const context = extend(options), attach = context.attach
    context.attach = value => { services.set(options.host.panelId, value); return attach(value) }; return context
  }
  const cleanup = dictate(scope)
  const pending = [services.get('main').startRecording('draft'), services.get('side').startRecording('draft')]
  const activity = host => registry.draw('composer.tool', { slot: 'state', target: 'draft', host }).at(-1)
  assert.equal(activity(main).active, true)
  assert.equal(activity(side).active, true)
  assert.equal(listeners.size, 2)
  registry.emit('field.reset', { host: side })
  assert.equal(activity(side), undefined)
  assert.equal(activity(main).active, true, 'a side navigation must leave main recording')
  assert.equal(main.draft.value, 'main draft')
  assert.equal(side.draft.value, 'side draft')
  assert.deepEqual(main.state, {})
  assert.deepEqual(side.state, {})
  cleanup(); scope.dispose()
  for (const finish of waiting) finish({ getTracks: () => [{ stop: () => stopped++ }] })
  await Promise.all(pending)
  assert.equal(stopped, 2, 'late permission responses release both disabled captures')
  assert.equal(listeners.size, 0)
  assert.equal(activity(main), undefined)
  assert.equal(activity(side), undefined)
  await loader.dispose()
})

test('turning Read aloud off invalidates a pending play response before it can start audio', async t => {
  let created = 0
  const previousAudio = globalThis.Audio
  globalThis.Audio = class { constructor() { created++ } }
  t.after(() => { if (previousAudio === undefined) delete globalThis.Audio; else globalThis.Audio = previousAudio })
  let service, finish
  const host = { schedule() {}, state: { sheet: null, speakMenu: null },
    fieldActivity() { return null },
    fetch: () => new Promise(resolve => { finish = resolve }),
  }
  const registry = createCoreRegistry()
  const loader = createPluginLoader({ registry, request: async () => ({ speech: true }), ui: { host } })
  const scope = loader.scope({ id: 'read-aloud' })
  const extend = scope.ui.extension
  scope.ui.extension = options => { const context = extend(options), attach = context.attach; context.attach = value => { service = value; return attach(value) }; return context }
  const cleanup = activate(scope)
  const take = { tkey: 'pending', chatId: 'example', agent: 'example', title: 'An answer', mode: 'verbatim', text: 'An answer.' }
  service.takes.set(take.tkey, take)
  const request = service.requestSpeech(take, { play: true })
  cleanup(); scope.dispose()
  finish(Response.json({ id: 'ready', status: 'ready', url: '/x/read-aloud/speech/ready/audio.mp3', duration_s: 10 }))
  const error = await request.then(() => null, error => error)
  assert.equal(take.phase, 'making', 'late response must not enter a playable state after disable')
  assert.equal(created, 0, 'disabled speech must not create an audio element for a late response')
  assert.equal(error, null)
  assert.equal(take.phase, 'making', 'late response was ignored instead of becoming ready/playing')
  assert.deepEqual(registry.draw('app.overlay', { slot: 'player' }), [])
})

test('disabling Dictation while microphone permission waits stops a late stream and removes device listeners', async t => {
  const { activate: dictate } = await import('../../plugins/_listed/dictation/page/main.js')
  let finish, service, stopped = 0
  const listeners = new Set(), previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: {
    getUserMedia: () => new Promise(resolve => { finish = resolve }),
    addEventListener: (name, callback) => listeners.add(callback),
    removeEventListener: (name, callback) => listeners.delete(callback),
  } } })
  t.after(() => { if (previousNavigator) Object.defineProperty(globalThis, 'navigator', previousNavigator); else delete globalThis.navigator })
  const registry = createCoreRegistry(), host = { state: {}, audioSessionType() {}, schedule() {}, activeThread: () => null, draft: { value: 'Keep this draft', isConnected: false }, fileField: {} }
  const loader = createPluginLoader({ registry, request: async () => ({}), ui: { host } })
  const scope = loader.scope({ id: 'dictation' }), extend = scope.ui.extension
  scope.ui.extension = options => {
    Object.assign(options.helpers, { readField: () => ({ caret: 5 }), caretPoint: () => null })
    const context = extend(options), attach = context.attach
    context.attach = value => { service = value; return attach(value) }; return context
  }
  const cleanup = dictate(scope), pending = service.startRecording('draft')
  assert.equal(listeners.size, 1)
  assert.equal(registry.draw('composer.tool', { slot: 'state', target: 'draft' })[0].active, true)
  cleanup(); scope.dispose()
  finish({ getTracks: () => [{ stop: () => stopped++ }] })
  await pending
  assert.equal(stopped, 1)
  assert.equal(listeners.size, 0)
  assert.equal(host.draft.value, 'Keep this draft')
  assert.deepEqual(registry.draw('composer.tool', { slot: 'state', target: 'draft' }), [])
  assert.equal('rec' in host.state, false)
  await loader.dispose()
})
