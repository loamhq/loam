import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { h, icon } from '../public/ui.js'
import { all, text } from './browser.js'
import { menuSurface } from '../public/menu-surface.js'
import { createVoicePicker } from '../public/plugins/read-aloud/page/voice-picker.js'
import { voiceLabel, FEMALE_VOICES } from '../public/plugins/read-aloud/page/voices.js'
import { shippedPlugins } from '../src/shipped-plugins.js'

function fixture(t, available = true) {
  installDom()
  const previous = globalThis.CSS; globalThis.CSS = { escape: value => value }
  t.after(() => { globalThis.CSS = previous })
  const audio = [], writes = []
  let redraws = 0, pauses = 0
  const picker = createVoicePicker({ ui: { h, icon, menuSurface }, redraw: () => redraws++, pauseSpeech: () => pauses++, samples: available ? { Charon: { file: '../samples/test.mp3', duration_s: 4 }, Kore: { file: '../samples/test-2.mp3', duration_s: 4 } } : {},
    makeAudio: url => {
      const element = new EventTarget()
      Object.assign(element, { url, plays: 0, pauses: 0, loads: 0, play() { this.plays++; return Promise.resolve() }, pause() { this.pauses++ }, load() { this.loads++ }, removeAttribute() {} })
      audio.push(element); return element
    } })
  const context = { agent: 'test-agent', plugin: { fields: [{ id: 'voice', default: 'Charon' }], values: { voice: 'Charon' } }, save: async value => { writes.push(value); return true }, defaultView: () => null }
  const draw = () => h('div', {}, picker.render(context))
  const button = (root, name) => all(root, 'voice-picker-play').find(n => n.getAttribute('aria-label') === name)
  const open = () => { all(draw(), 'voice-picker-trigger')[0].fire('click'); return draw() }
  return { picker, context, audio, writes, draw, button, open, get pauses() { return pauses }, get redraws() { return redraws } }
}

test('all thirty manifest labels include the supplied gender without changing voice ids or default', async () => {
  const plugin = (await shippedPlugins('public')).find(p => p.id === 'read-aloud')
  const voice = plugin.agent_settings.find(f => f.id === 'voice')
  assert.equal(voice.choices.length, 30); assert.equal(FEMALE_VOICES.size, 14)
  assert.equal(voice.default, 'Charon')
  for (const choice of voice.choices) assert.equal(choice.label, voiceLabel(choice.id))
  assert.equal(voiceLabel('Charon'), 'Charon · Male'); assert.equal(voiceLabel('Kore'), 'Kore · Female')
})

test('sample toggle and switching release the previous audio, never change the setting, and reset on ended', async t => {
  const f = fixture(t), root = f.open()
  f.button(root, 'Play Charon sample').fire('click'); await Promise.resolve()
  assert.equal(f.audio[0].plays, 1); assert.equal(f.pauses, 1)
  assert.match(f.audio[0].url, /\/samples\/test.mp3$/)
  f.button(f.draw(), 'Stop Charon sample').fire('click')
  assert.equal(f.audio[0].pauses, 1); assert.equal(f.audio[0].loads, 1)
  f.button(f.draw(), 'Play Charon sample').fire('click')
  f.button(f.draw(), 'Play Kore sample').fire('click')
  assert.equal(f.audio[1].pauses, 1)
  f.audio[2].dispatchEvent(new Event('ended'))
  assert.ok(f.button(f.draw(), 'Play Kore sample'))
  assert.deepEqual(f.writes, [])
})

test('choosing sends a bare id through the normal save callback, stops audio and closes', async t => {
  const f = fixture(t), root = f.open()
  f.button(root, 'Play Charon sample').fire('click')
  all(root, 'voice-picker-choice').find(n => n.getAttribute('aria-label') === 'Choose Kore · Female').fire('click')
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(f.writes, [{ voice: 'Kore' }]); assert.equal(f.audio[0].pauses, 1)
  assert.equal(all(f.draw(), 'agent-voice-picker').length, 0)
})

test('dismissal and removing the profile stop even a pending play; late errors cannot reopen it', async t => {
  const f = fixture(t), root = f.open()
  f.button(root, 'Play Charon sample').fire('click')
  f.picker.beforeRender(); f.picker.afterRender()
  assert.equal(f.audio[0].pauses, 1)
  f.audio[0].dispatchEvent(new Event('error'))
  assert.equal(all(f.draw(), 'agent-voice-picker').length, 0)
})

test('save refusal preserves the picker, current selection and authoritative error', async t => {
  const f = fixture(t); f.context.save = async () => false
  const root = f.open()
  all(root, 'voice-picker-choice')[0].fire('click'); await Promise.resolve()
  f.context.error = 'That choice is unavailable.'
  assert.match(text(f.draw()), /That choice is unavailable\./)
  assert.equal(all(f.draw(), 'voice-picker-choice').find(n => n.getAttribute('aria-pressed') === 'true').getAttribute('aria-label'), 'Choose Charon · Male')
})

test('a stale save cannot dismiss a newly opened picker', async t => {
  const f = fixture(t); let finish
  f.context.save = () => new Promise(resolve => { finish = resolve })
  all(f.open(), 'voice-picker-choice')[0].fire('click')
  f.picker.close(); f.context.agent = 'another-agent'; f.open()
  finish(true); await Promise.resolve()
  assert.equal(all(f.draw(), 'agent-voice-picker').length, 1)
})

test('play errors stay local and missing recordings have no playable source', async t => {
  const f = fixture(t), root = f.open()
  f.button(root, 'Play Charon sample').fire('click')
  f.audio[0].dispatchEvent(new Event('error'))
  assert.match(text(f.draw()), /Couldn’t play Charon’s sample/)
  assert.ok(f.button(f.draw(), 'Play Charon sample'))
  const unavailable = fixture(t, false), empty = unavailable.open()
  assert.match(text(empty), /haven’t been recorded yet/)
  assert.ok(all(empty, 'voice-picker-play').every(n => n.hasAttribute('disabled')))
})
