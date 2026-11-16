// The response set, the settings and the clock — `public/speech.js` with no
// page around it.

import test from 'node:test'
import assert from 'node:assert/strict'

import { responseSets, speechSettings, nextRate, clock, wordCount, wordsLabel, listenEstimate } from '../public/plugins/read-aloud/page/speech.js'

const user = (text) => ({ what: 'user', item: { kind: 'user', text } })
const say = (text) => ({ what: 'text', block: { type: 'text', text } })
const result = () => ({ what: 'result', item: { kind: 'result' } })
const step = (name) => ({ what: 'step', block: { type: 'tool', name } })
const answer = () => ({ what: 'answer', item: { kind: 'answer' } })

test('one turn: its words, and the glyphs under its turn line', () => {
  const flat = [user('hi'), say('  Hello there.  '), result()]
  const [set] = responseSets(flat)
  assert.equal(set.text, 'Hello there.')
  assert.equal(set.words, 2)
  assert.equal(set.anchor, 2)
  assert.equal(set.first, flat[1])
})

test('several turns up to the next message, joined by blank lines; a fold and a question card between them are not read', () => {
  const flat = [
    user('plan?'),
    say('One.'), step('Bash'), step('Read'), result(),
    answer(),
    step('thinking'), say('Two.'), result(),
    say('Three.'), result(),
    user('thanks'),
    say('Welcome.'),
  ]
  const sets = responseSets(flat)
  assert.equal(sets.length, 2)
  assert.equal(sets[0].text, 'One.\n\nTwo.\n\nThree.')
  assert.equal(sets[0].anchor, 10, 'not under the set’s last turn line')
  assert.equal(sets[0].end, 11)
  // Still being written: no result after the words, so no anchor.
  assert.equal(sets[1].text, 'Welcome.')
  assert.equal(sets[1].anchor, null)
  assert.equal(sets[1].end, 13)
})

test('words after the last turn line move the glyphs to the set’s end; a set with no words is no set', () => {
  const sets = responseSets([user('a'), say('x'), result(), say('y'), user('b'), step('Bash'), result()])
  assert.equal(sets.length, 1)
  assert.equal(sets[0].anchor, null)
  assert.equal(sets[0].text, 'x\n\ny')
})

test('a window opened mid-set: the lines above the first message are a set of their own', () => {
  const flat = [say('Tail of an answer.'), result(), user('next')]
  assert.equal(responseSets(flat)[0].text, 'Tail of an answer.')
})

test('settings are cleaned: steps, floors, ceilings, and the three rates', () => {
  assert.deepEqual(speechSettings(null), { listenFrom: 50, briefFrom: 200, rate: 1 })
  assert.deepEqual(speechSettings({ listenFrom: 3, briefFrom: 5000, rate: 3 }), { listenFrom: 10, briefFrom: 1000, rate: 1 })
  assert.deepEqual(speechSettings({ listenFrom: 64, briefFrom: 230, rate: 1.5 }), { listenFrom: 60, briefFrom: 250, rate: 1.5 })
  assert.deepEqual([1, 1.5, 2].map(nextRate), [1.5, 2, 1])
})

test('the clock and the words', () => {
  assert.equal(clock(312.4), '5:12')
  assert.equal(clock(3723), '1:02:03')
  assert.equal(clock(NaN), '–:––')
  assert.equal(clock(null), '–:––')
  assert.equal(wordCount(' a  b\n\nc '), 3)
  assert.equal(wordsLabel(1000), '1,000 words')
})

test('the Listen estimate: about 150 words a minute, said as a person says a length', () => {
  assert.equal(listenEstimate(1000), 'about 7 min')
  assert.equal(listenEstimate(100), 'about 40 s')
  assert.equal(listenEstimate(10), 'about 10 s')
  assert.equal(listenEstimate(200), 'about 1½ min')
  assert.equal(listenEstimate(150), 'about 1 min')
  assert.equal(listenEstimate(750), 'about 5 min')
})
