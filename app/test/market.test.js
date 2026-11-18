// The Market's reading of the list (public/market.js, 2026-11-18): search is
// ask-first and forgiving, the lead story and sections follow the list, and
// a thin or empty list draws nothing it does not have.
import test from 'node:test'
import assert from 'node:assert/strict'
import { stem, search, highlight, leadOf, sectionsOf, kindsListed, dateline, buildRequest, actionOf, plainTouches, reachOf } from '../public/market.js'
import { LISTINGS } from './market-fixture.mjs'

const ids = list => list.map(e => e.id)

test('a word and its -ing, -s and -e forms meet', () => {
  for (const [a, b] of [['write', 'writing'], ['write', 'writes'], ['run', 'running'], ['sync', 'syncing'], ['theme', 'themes'], ['story', 'stories'], ['picture', 'pictures']]) assert.equal(stem(a), stem(b), `${a} / ${b}`)
  assert.notEqual(stem('note'), stem('nothing'))
})

test('"write" finds a listing that says "writing", and one that says "write"', () => {
  const found = ids(search(LISTINGS, 'write'))
  assert.ok(found.includes('essay-shaping'), 'essay-shaping says "writing"')
  assert.ok(found.includes('story-desk'), 'story-desk says "write"')
  assert.ok(!found.includes('ember'))
})

test('a plain-language query finds the obvious thing first', () => {
  assert.equal(search(LISTINGS, 'sync with my phone')[0].id, 'pocket-sync')
  assert.equal(search(LISTINGS, 'make pictures')[0].id, 'images')
  assert.equal(search(LISTINGS, 'a calmer theme')[0].id, 'moss')
  assert.equal(search(LISTINGS, 'running coach')[0].id, 'running-coach')
})

test('search reads name, description, author and kind', () => {
  assert.deepEqual(ids(search(LISTINGS, 'Ada Park')).sort(), ids(LISTINGS.filter(e => e.author === 'Ada Park')).sort())
  assert.deepEqual(ids(search(LISTINGS, 'themes')).sort(), ['ember', 'moss', 'tide'])
  assert.deepEqual(ids(search(LISTINGS, 'habit wall')), ['habit-wall'])
})

test('a kind narrows a search, and an empty query is the whole kind', () => {
  assert.deepEqual(ids(search(LISTINGS, '', 'app')), ['story-desk', 'habit-wall'])
  assert.deepEqual(ids(search(LISTINGS, 'write', 'skill')), ['essay-shaping'])
})

test('no match is no results: the page shows the build-it panel instead', () => {
  assert.deepEqual(search(LISTINGS, 'a zebra that tells jokes'), [])
  assert.deepEqual(search([], 'anything'), [])
  assert.equal(buildRequest('a zebra that tells jokes'), 'Could you build me my own plugin for this: “a zebra that tells jokes”? I looked in the Market and nothing was quite right.')
  assert.match(buildRequest('', 'theme'), /^Could you build me my own theme\? Here’s what I have in mind: $/)
})

test('the words typed are marked, in any of their forms', () => {
  const parts = highlight('Teaches every agent one way to take writing from notes', 'write')
  assert.deepEqual(parts.filter(p => p.hit).map(p => p.text), ['writing'])
  assert.equal(parts.map(p => p.text).join(''), 'Teaches every agent one way to take writing from notes')
  assert.deepEqual(highlight('Pocket Sync', 'sync with my phone').filter(p => p.hit).map(p => p.text), ['Sync'])
  assert.deepEqual(highlight('Nothing here', ''), [{ text: 'Nothing here', hit: false }])
})

test('the lead story is the featured listing, else one of Loam’s own', () => {
  assert.equal(leadOf(LISTINGS).id, 'images')
  const unfeatured = LISTINGS.map(({ featured, ...e }) => e)
  assert.equal(leadOf(unfeatured).author, 'Loam contributors')
  assert.equal(leadOf(unfeatured.filter(e => e.author !== 'Loam contributors')), null)
})

test('sections are the kinds that have listings, without the lead story', () => {
  const lead = leadOf(LISTINGS), sections = sectionsOf(LISTINGS, lead)
  assert.deepEqual(sections.map(s => s.kind), ['theme', 'extension', 'app', 'agent', 'skill'])
  assert.ok(!sections.flatMap(s => s.entries).some(e => e.id === lead.id))
  const thin = LISTINGS.filter(e => ['ember', 'pocket-sync'].includes(e.id))
  assert.deepEqual(sectionsOf(thin, null).map(s => s.kind), ['theme', 'extension'])
  assert.deepEqual(kindsListed(thin), ['theme', 'extension'])
})

test('an empty list draws no lead, no section and no kinds', () => {
  assert.equal(leadOf([]), null)
  assert.deepEqual(sectionsOf([], null), [])
  assert.deepEqual(kindsListed([]), [])
  assert.match(dateline(0, new Date('2026-11-18T20:00:00Z')), /Nothing listed yet$/)
  assert.match(dateline(11, new Date('2026-11-18T20:00:00Z')), /11 things listed, each read by hand$/)
})

test('a listing’s button follows what is installed', () => {
  const theme = LISTINGS.find(e => e.id === 'ember'), ext = LISTINGS.find(e => e.id === 'images'), app = LISTINGS.find(e => e.id === 'story-desk'), agent = LISTINGS.find(e => e.id === 'running-coach')
  assert.deepEqual(actionOf(theme), { action: 'install', label: 'Get' })
  assert.deepEqual(actionOf(theme, { installed: true, themeInUse: 'ember' }), { action: 'none', label: 'In use' })
  assert.equal(actionOf(theme, { installed: true, themeInUse: 'latte' }).label, 'Use')
  assert.deepEqual(actionOf(ext), { action: 'install', label: 'Install' })
  assert.deepEqual(actionOf(ext, { installed: true }), { action: 'none', label: 'Installed' })
  assert.deepEqual(actionOf(app, { installed: true }), { action: 'open', label: 'Open' })
  assert.deepEqual(actionOf(agent), { action: 'view', label: 'See it' })
})

test('what a plugin touches, in plain words', () => {
  const images = plainTouches(LISTINGS[0].declaration, 'extension').map(l => l.text)
  assert.deepEqual(images, ['Talks to api.openai.com', 'Runs a small program on your server', 'Gives your agents a new tool', 'Uses your OpenAI key'])
  assert.deepEqual(plainTouches({ touches: {}, page_code: false }, 'theme').map(l => l.text), ['Only colours and type'])
  assert.equal(reachOf(LISTINGS[0].declaration).text, 'Talks to one service')
  assert.equal(reachOf({ touches: { server: true } }).text, 'Stays on your server')
})

test('a listed theme’s CSS is held to an installed theme’s rules before the page wears it', async () => {
  const { marketThemeCSS } = await import('../src/themes.js')
  const { readFile } = await import('node:fs/promises')
  const css = (await readFile('public/themes/autumn/theme.css', 'utf8')).replaceAll('data-look="autumn"', 'data-look="ember"')
  const out = await marketThemeCSS('ember', css)
  assert.match(out, /\.theme-preview\[data-look="ember"\]/, 'its swatch scope comes with it')
  await assert.rejects(marketThemeCSS('ember', ':root[data-look="ember"] { --paper-page: url(https://example.invalid/x.png); }'), /Invalid theme value/)
  await assert.rejects(marketThemeCSS('ember', 'body { color: red; }'), /Invalid theme/)
  await assert.rejects(marketThemeCSS('Ember!', css), /Invalid theme id/)
})
