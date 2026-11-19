import test from 'node:test'
import assert from 'node:assert/strict'
import { mount } from '../../plugins/_listed/read-aloud/page/engine.js'

test('Full take identity and growing-response fallback preserve both description preference and voice/style', () => {
  const host = { store: { readAloud: { describeTablesAndDiagrams: true } } }
  const service = mount(host)
  const request = { voice: 'Kore', style: 'calm' }
  const context = { id: 'thread-reply', request }
  const set = { text: 'An answer.', context }
  const key = service.takeKeyOf(context.id, 'verbatim', set.text, request)
  const take = { tkey: key, chatId: context.id, mode: 'verbatim', text: set.text, request,
    describeTablesAndDiagrams: true, open: true, serial: 1 }
  service.takes.set(key, take)
  assert.equal(service.findTake(set, 'verbatim'), take)
  const grown = { ...set, text: set.text + ' More follows.' }
  assert.equal(service.findTake(grown, 'verbatim'), take)
  const briefKey = service.takeKeyOf(context.id, 'brief', set.text, request)
  host.store.readAloud.describeTablesAndDiagrams = false
  assert.notEqual(service.takeKeyOf(context.id, 'verbatim', set.text, request), key)
  assert.equal(service.findTake(set, 'verbatim'), null)
  assert.equal(service.findTake(grown, 'verbatim'), null)
  assert.equal(service.takeKeyOf(context.id, 'brief', set.text, request), briefKey)
  // An existing take retains its captured options when regenerated, even if
  // the device setting changes while that recording is playing.
  assert.deepEqual(service.speechBody(take), { chat_id: context.id, agent: undefined, mode: 'verbatim', title: undefined,
    text: set.text, ...request, describe_tables_and_diagrams: true })
  host.store.readAloud.describeTablesAndDiagrams = true
  for (const changed of [{ voice: 'Charon', style: 'calm' }, { voice: 'Kore', style: 'bright' }]) {
    assert.notEqual(service.takeKeyOf(context.id, 'verbatim', set.text, changed), key)
    assert.equal(service.findTake({ ...grown, context: { ...context, request: changed } }, 'verbatim'), null)
  }
})
