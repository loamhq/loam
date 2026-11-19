// Install from a link (2026-11-18): what the page makes of a pasted link
// before the host reads it (public/market.js `linkShape`), the card an agent
// writes for a plugin by its link (public/market-card.js), and the address
// that opens the link sheet (public/app-modal-history.js). The host's own
// reading of every shape is tests/test_plugins.py; the sheet itself is
// test/link-install-browser.mjs.
import test from 'node:test'
import assert from 'node:assert/strict'
import { linkShape } from '../public/market.js'
import { cardRef, splitCards, linkAddress } from '../public/market-card.js'
import { modalFromURL, modalURL } from '../public/app-modal-history.js'

test('every shape a person pastes is a GitHub repository', () => {
  for (const [text, repo] of [
    ['friend/loam-pet', 'friend/loam-pet'],
    ['https://github.com/loamhq/plugins', 'loamhq/plugins'],
    ['github.com/loamhq/plugins/', 'loamhq/plugins'],
    ['https://www.github.com/loamhq/plugins.git', 'loamhq/plugins'],
    ['git@github.com:loamhq/plugins.git', 'loamhq/plugins'],
    ['  <https://github.com/a/b/tree/autumn-v1.0.1/autumn?tab=readme#x>  ', 'a/b'],
    ['https://github.com/a/b/releases/tag/v1.2.0', 'a/b'],
    ['https://github.com/a/b/blob/v1/x/PLUGIN.md', 'a/b'],
    ['https://raw.githubusercontent.com/a/b/v1/x/PLUGIN.md', 'a/b'],
    ['loamhq/plugins/tree/main/images', 'loamhq/plugins'],
  ]) assert.deepEqual(linkShape(text), { ok: true, repo }, text)
})

test('what plainly is not one says so; what is half typed waits', () => {
  for (const text of ['https://gitlab.com/a/b', 'example.com/x', 'a theme I saw', 'https://example.com']) assert.deepEqual(linkShape(text), { ok: false }, text)
  for (const text of ['', '   ', 'git', 'github.com', 'https://github.com/', 'github.com/loamhq', 'https://']) assert.equal(linkShape(text), null, text)
})

test('a card by link: the line agents write, and nothing near it', () => {
  const link = 'https://github.com/friend/lantern'
  const line = `[Lantern](${linkAddress('https://loam.example', link)})`
  assert.deepEqual(cardRef(line), { link, label: 'Lantern' })
  assert.deepEqual(cardRef('[Lantern](https://loam.example/?modal=market&install=friend%2Flantern)'), { link: 'friend/lantern', label: 'Lantern' })
  for (const bad of [
    '[x](https://loam.example/?modal=market&install=https%3A%2F%2Fevil.example%2Fa%2Fb)',
    '[x](https://loam.example/?modal=market&install=javascript%3Aalert(1))',
    '[x](https://loam.example/?modal=market&install=friend%2Flantern&listing=a)',
    '[x](https://loam.example/x?modal=market&install=friend%2Flantern)',
  ]) assert.equal(cardRef(bad), null, bad)
  const pieces = splitCards(`Here it is:\n\n${line}\n\nTap Install to see what it touches.`)
  assert.deepEqual(pieces.map(p => p.cards ? 'cards' : 'text'), ['text', 'cards', 'text'])
  assert.deepEqual(pieces[1].cards, [{ link, label: 'Lantern' }])
})

test('?modal=market&install=<link> opens the link sheet over the Market, and is not kept', () => {
  const url = 'https://app.example/?modal=market&install=https%3A%2F%2Fgithub.com%2Floamhq%2Fplugins'
  assert.deepEqual(modalFromURL(url), { kind: 'market', view: 'front', id: null, k: 'all', q: '', install: 'https://github.com/loamhq/plugins' })
  assert.equal(modalFromURL('https://app.example/?modal=market&install=https%3A%2F%2Fevil.example%2Fx').install, undefined)
  assert.equal(modalURL(url, { kind: 'market', view: 'front', k: 'all' }).searchParams.has('install'), false)
})
