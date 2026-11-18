// A listing card in an agent's reply (2026-11-18): the form an agent writes
// (public/market-card.js), the card the page draws for it (page/market.js
// `marketCards`), and the agent's search (bin/loam-market search) ranking
// exactly as the Market's own field does.
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { installDom } from './fakedom.js'
import { cardRef, splitCards, listingAddress, MARKET_CARDS_MAX } from '../public/market-card.js'
import { search } from '../public/market.js'
import { LISTINGS, cardLine } from './market-fixture.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '../..')

test('a card is the listing’s name linked to its page in the app, on a line of its own', () => {
  assert.equal(listingAddress('https://loam.example/', 'pocket-sync'), 'https://loam.example/?modal=market&listing=pocket-sync')
  assert.deepEqual(cardRef('[Pocket Sync](https://loam.example/?modal=market&listing=pocket-sync)'), { id: 'pocket-sync', label: 'Pocket Sync' })
  assert.deepEqual(cardRef('  [Moss](<https://a.b/?listing=moss&modal=market>)  '), { id: 'moss', label: 'Moss' }, 'either order, angle brackets, spaces around')
  for (const [why, line] of [
    ['inside a sentence', 'See [Moss](https://a.b/?modal=market&listing=moss) for this'],
    ['a list item', '- [Moss](https://a.b/?modal=market&listing=moss)'],
    ['an image', '![Moss](https://a.b/?modal=market&listing=moss)'],
    ['another path', '[Moss](https://a.b/threads/x?modal=market&listing=moss)'],
    ['another key', '[Moss](https://a.b/?modal=market&listing=moss&q=x)'],
    ['a fragment', '[Moss](https://a.b/?modal=market&listing=moss#top)'],
    ['the Market at large', '[Market](https://a.b/?modal=market)'],
    ['a search', '[Market](https://a.b/?modal=market&q=moss)'],
    ['an id that is not one', '[Moss](https://a.b/?modal=market&listing=Moss!)'],
    ['another scheme', '[Moss](javascript:alert(1)//?modal=market&listing=moss)'],
    ['a relative address', '[Moss](/?modal=market&listing=moss)'],
  ]) assert.equal(cardRef(line), null, why)
})

test('a reply is cut into words and cards, in order; text with none is untouched', () => {
  const text = `Two that suit the evenings:\n\n${cardLine('Moss', 'moss')}\n\n${cardLine('Tide', 'tide')}\n\nYou can try either.`
  assert.deepEqual(splitCards(text), [
    { text: 'Two that suit the evenings:' },
    { cards: [{ id: 'moss', label: 'Moss' }, { id: 'tide', label: 'Tide' }] },
    { text: 'You can try either.' },
  ])
  const plain = 'Nothing here.\n\n```\nx\n```'
  assert.deepEqual(splitCards(plain), [{ text: plain }])
  const fenced = `Write it like this:\n\n\`\`\`\n${cardLine('Moss', 'moss')}\n\`\`\``
  assert.deepEqual(splitCards(fenced), [{ text: fenced }], 'a line in fenced code is code')
  const many = ['a', 'b', 'c', 'd'].map(id => cardLine(id.toUpperCase(), `x${id}`)).join('\n')
  const cut = splitCards(many)
  assert.equal(cut[0].cards.length, MARKET_CARDS_MAX)
  assert.deepEqual(cut[1], { text: cardLine('D', 'xd') }, 'past three, a line stays a link')
})

// The page's half: an id the Market lists is a card; one it does not is the
// link's words, plain, with no link and no error.
function cardsHost(list) {
  installDom()
  const schedules = []
  return {
    state: { sheet: null, wide: true, agents: { list: [] } },
    theme: { value: { theme: 'latte' }, installed: [], trying: null, try() {}, set() {} },
    pluginLoader: { st: { rows: [] } },
    schedule: () => schedules.push(1),
    api: async (method, route) => route === '/marketplace' ? list : { declaration: { touches: { network: [], server: false } }, icon: false },
    openSheet() {}, closeSheet() {}, goApps() {}, goSettings() {},
  }
}
const textOf = node => node.nodeType === 3 ? node.nodeValue : [...(node.childNodes ?? [])].map(textOf).join('')
const findAll = (node, test, out = []) => { if (node.nodeType === 1 && test(node)) out.push(node); for (const c of node.childNodes ?? []) findAll(c, test, out); return out }

test('an unknown listing id is drawn as its words, plain: no card, no link, no error', async () => {
  const { mount } = await import('../public/page/market.js')
  const host = cardsHost({ state: 'listed', plugins: LISTINGS.map(({ declaration, guide, icon, from, installable, why, ...e }) => ({ ...e, installed: false })) })
  const market = mount(host)
  await market.loadMarket()
  const unknown = market.marketCards([{ id: 'nightjar', label: 'Nightjar' }], { from: 'A thread', key: 'm9' })
  assert.equal(unknown.nodeName.toLowerCase(), 'p')
  assert.equal(textOf(unknown), 'Nightjar')
  assert.equal(findAll(unknown, n => ['a', 'button', 'article'].includes(n.nodeName.toLowerCase())).length, 0)
  const known = market.marketCards([{ id: 'pocket-sync', label: 'Pocket Sync' }, { id: 'nightjar', label: 'Nightjar' }], { from: 'A thread', key: 'm6' })
  const cards = findAll(known, n => n.nodeName.toLowerCase() === 'article')
  assert.equal(cards.length, 1)
  assert.match(textOf(cards[0]), /Pocket Sync/)
  assert.match(textOf(cards[0]), /Install/)
  assert.ok(findAll(known, n => n.nodeName.toLowerCase() === 'p' && textOf(n) === 'Nightjar').length, 'the unknown one beside it is its words')
  const off = cardsHost({ state: 'off', plugins: [] })
  const offMarket = mount(off)
  await offMarket.loadMarket()
  assert.equal(textOf(offMarket.marketCards([{ id: 'pocket-sync', label: 'Pocket Sync' }], { key: 'x' })), 'Pocket Sync', 'a Market that is off: words')
})

// The agent's half: bin/loam-market search ranks with the page's own
// function, over the same list the broker hands the page.
test('the agent’s search and the Market page rank the same query the same', { timeout: 60000 }, () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'loam-market-card-'))
  try {
    const keep = ['id', 'name', 'author', 'kind', 'description', 'link', 'version', 'featured']
    writeFileSync(path.join(dir, 'list.json'), JSON.stringify({ version: 1, plugins: LISTINGS.map(e => Object.fromEntries(keep.filter(k => k in e).map(k => [k, e[k]]))) }))
    writeFileSync(path.join(dir, 'loam.toml'), `[paths]\nhome = "${dir}/home"\nplugins = "${dir}/plugins"\nstate = "${dir}/state"\n[marketplace]\nlist = "file://${dir}/list.json"\n`)
    const env = { ...process.env, LOAM_CONFIG: path.join(dir, 'loam.toml'), PYTHONDONTWRITEBYTECODE: '1' }
    const market = (...args) => JSON.parse(execFileSync('python3', [path.join(ROOT, 'bin/loam-market'), ...args], { env, encoding: 'utf8' }))
    // What the broker answers the page (GET /v1/marketplace is this).
    const page = market('list', '--json').plugins
    assert.equal(page.length, LISTINGS.length)
    for (const [q, kind] of [['sync with my phone', 'all'], ['write', 'all'], ['a calmer theme', 'all'], ['make pictures', 'all'], ['dark', 'theme'],
      ['notes', 'all'], ['Ada Park', 'all'], ['a zebra that tells jokes', 'all'], ['', 'skill'], ['story', 'app'], ['coach', 'agent']]) {
      const got = market('search', '--json', '--limit', '20', '--kind', kind, '--', ...q.split(' ').filter(Boolean))
      const want = search(page, q, kind).map(e => e.id)
      assert.deepEqual(got.results.map(r => r.id), want, `“${q}” in ${kind}`)
      assert.equal(got.count, want.length)
    }
    const one = market('search', '--json', '--', 'sync', 'with', 'my', 'phone').results[0]
    assert.deepEqual(Object.keys(one).sort(), ['author', 'card', 'description', 'id', 'installable', 'installed', 'kind', 'name'])
    assert.deepEqual(cardRef(one.card), { id: 'pocket-sync', label: 'Pocket Sync' }, 'its card line is a card')
    const coach = market('search', '--json', '--', 'coach').results[0]
    assert.equal(coach.installable, false)
    assert.match(coach.why_not, /installs none yet/)
    const none = market('search', '--json', '--', 'a', 'zebra')
    assert.match(none.say, /Offer to build it/)
    // An empty Market: nothing, said plainly.
    writeFileSync(path.join(dir, 'list.json'), JSON.stringify({ version: 1, plugins: [] }))
    const empty = market('search', '--json', '--refresh', '--', 'anything')
    assert.deepEqual([empty.state, empty.results, empty.count], ['empty', [], 0])
    assert.match(empty.say, /The Market lists nothing yet\. Offer to build it/)
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
