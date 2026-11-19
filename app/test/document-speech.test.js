import test from 'node:test'
import assert from 'node:assert/strict'
import { documentSpeakable, documentWords, documentStops, readAloudSettings } from '../../plugins/_listed/read-aloud/page/document-speech.js'
import { createPreferences } from '../../plugins/_listed/read-aloud/page/settings.js'
import { pluginSettings } from '../public/plugin-loader.js'
const createStore = storage => createPreferences(pluginSettings('read-aloud', storage))
import { storage } from './browser.js'

test('a 200-word note offers 30 seconds, one minute, and Full; only Full is verbatim', () => {
  const stops = documentStops(200)
  assert.deepEqual(stops.map((s) => s.minutes), [0.5, 1, 'full'])
  assert.deepEqual(stops.map((s) => s.mode), ['brief', 'brief', 'verbatim'])
  assert.deepEqual(stops.map((s) => s.words), [75, 150, 200])
  assert.equal(stops[1].label, '1 min')
  assert.equal(stops.at(-1).label, 'Full · 1 min')
  assert.deepEqual(stops.map((s) => s.short), ['30 s', '1 min', 'Full'])
})

test('15,000 words: a three-minute brief budgets 450 words; full is 100 minutes', () => {
  const stops = documentStops(15_000)
  assert.deepEqual(stops.map((s) => s.minutes), [1, 3, 5, 10, 20, 30, 45, 60, 'full'])
  assert.equal(stops.find((s) => s.minutes === 3).words, 450)
  assert.equal(stops.find((s) => s.minutes === 10).label, '10 min')
  assert.equal(stops.at(-1).label, 'Full · 100 min')
})

test('a roughly 21-minute note has short track labels and a rounded Full readout', () => {
  const stops = documentStops(3105)
  assert.deepEqual(stops.map((s) => s.short), ['1 min', '3 min', '5 min', '10 min', 'Full'])
  assert.deepEqual(stops.map((s) => s.label), ['1 min', '3 min', '5 min', '10 min', 'Full · 21 min'])
})

test('tiny notes offer Full alone; every brief is shorter and budgets at most 75 percent', () => {
  for (const words of [0, 1, 40, 99, 100, 200, 450, 7200, 15000, 60000]) {
    const stops = documentStops(words)
    assert.equal(stops.at(-1).mode, 'verbatim')
    assert.equal(stops.at(-1).minutes, 'full')
    for (const stop of stops.slice(0, -1)) {
      assert.equal(stop.mode, 'brief')
      assert.ok(stop.words <= words * 0.75)
    }
  }
  assert.deepEqual(documentStops(40).map((s) => s.minutes), ['full'])
})

test('document speech drops frontmatter, names tables and code, and reads wiki titles and aliases', () => {
  const note = '\uFEFF---\r\nprivate: not spoken\r\n---\r\n# The proposal\n\nRead [[Plans/The foundation.md#Costs]] and [[Elsewhere/Note|the budget]].\n\n- [ ] Decide [today](https://example.com).\n\n| Name | Cost |\n| --- | --- |\n| Plan | 20 |\n\n```js\nsecretCode()\n```'
  const spoken = documentSpeakable(note)
  assert.ok(!spoken.includes('private') && !spoken.includes('secretCode') && !spoken.includes('https'))
  assert.match(spoken, /The proposal\./)
  assert.match(spoken, /Read The foundation and the budget/)
  assert.match(spoken, /Decide today/)
  assert.match(spoken, /A table of one row, skipped/)
  assert.match(spoken, /A code block of one line, skipped/)
  assert.equal(documentWords('---\nmetadata: ignored\n---\n**one** [[Folder/two]] [three](https://example.com)'), 3)
})

test('Files voice persists on this device and invalid settings fall back to Aoede', () => {
  const held = storage()
  const store = createStore(held)
  assert.deepEqual(store.readAloud, { voice: 'Aoede', describeTablesAndDiagrams: true })
  store.readAloud = { voice: 'Kore' }
  assert.deepEqual(createStore(held).readAloud, { voice: 'Kore', describeTablesAndDiagrams: true })
  assert.deepEqual(JSON.parse(held.getItem('loam-ui.read-aloud')), { voice: 'Kore', describeTablesAndDiagrams: true })
  assert.deepEqual(readAloudSettings({ voice: 'invented' }), { voice: 'Aoede', describeTablesAndDiagrams: true })
})

test('existing device settings default descriptions on and an explicit off survives reload', () => {
  const held = storage()
  held.setItem('loam-ui.read-aloud', JSON.stringify({ voice: 'Kore' }))
  const store = createStore(held)
  assert.equal(store.readAloud.describeTablesAndDiagrams, true)
  store.readAloud = { ...store.readAloud, describeTablesAndDiagrams: false }
  assert.deepEqual(createStore(held).readAloud, { voice: 'Kore', describeTablesAndDiagrams: false })
})

test('Files estimate counts thirty words per table or diagram only when descriptions are on', () => {
  const text = '| Plan |\n| --- |\n| Pro |\n\n```mermaid\na-->b\n```\n\n```js\nf()\n```'
  assert.equal(documentWords(text), 60 + 7)
  assert.equal(documentWords(text, { describeTablesAndDiagrams: false }), documentSpeakable(text).split(/\s+/).length)
})
