// The app's one empty state (public/empty-state.js, 2026-11-18): Loam's
// blob holding one fine-line object, a title, one line, at most one action;
// every paint a class on a token, nothing that reads as clip-art. The motion
// and its reduced-motion form are proved in a browser by
// test/empty-state-browser.mjs.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { installDom } from './fakedom.js'
import { find, text } from './browser.js'
import { h } from '../public/ui.js'
import { emptyState, emptyArt, EMPTY_OBJECTS, BLOB } from '../public/empty-state.js'

const kids = node => [...node.childNodes].filter(n => n.nodeType === 1)
const cls = node => node.getAttribute('class') ?? ''

test('each place has its own object, and every one sits in the same blob', () => {
  installDom()
  assert.deepEqual(EMPTY_OBJECTS, ['sprout', 'search', 'page', 'speech', 'archive', 'folder', 'window', 'clock', 'moon'])
  for (const object of EMPTY_OBJECTS) {
    const art = emptyArt(object)
    assert.equal(art.getAttribute('aria-hidden'), 'true', `${object}: the drawing is decoration`)
    assert.equal(art.getAttribute('viewBox'), '0 0 164 140')
    assert.match(cls(art), new RegExp(`\\bempty-state-${object}\\b`))
    const [blob, ...rest] = kids(art)
    assert.equal(blob.getAttribute('d'), BLOB, `${object}: the blob comes first`)
    assert.ok(rest.length >= 2, `${object}: an object in it`)
    // Paints are classes, never a colour or a style on the node.
    for (const node of [blob, ...rest]) {
      assert.match(cls(node), /\bes-[a-z]/, `${object}: every shape is painted by a class`)
      for (const name of ['fill', 'stroke', 'style', 'color']) assert.equal(node.getAttribute(name), null, `${object}: no ${name} attribute`)
    }
    assert.ok(rest.some(n => /\bes-accent/.test(cls(n))), `${object}: one accent in espresso`)
  }
  assert.throws(() => emptyArt('confetti'), /no object called “confetti”/)
})

test('the words: a display title, one line, at most one action, in that order', () => {
  installDom()
  const go = h('button', { class: 'pillbtn primary', text: 'Build an app' })
  const node = emptyState({ object: 'window', title: 'No apps yet', body: 'An agent can build an app for you.', action: go, props: { class: 'apps-empty', key: 'k' } })
  assert.match(cls(node), /^empty-state empty-state-pane apps-empty$/)
  assert.equal(node.getAttribute('data-empty'), 'window')
  const [art, title, body, action] = kids(node)
  assert.match(cls(art), /empty-state-art/)
  assert.equal(title.nodeName.toLowerCase(), 'h2'); assert.match(cls(title), /\bdisplay\b/)
  assert.equal(text(title), 'No apps yet')
  assert.equal(text(body), 'An agent can build an app for you.')
  assert.equal(kids(action)[0], go)
  assert.equal(text(node), 'No apps yetAn agent can build an app for you.Build an app')

  const bare = emptyState({ object: 'folder', size: 'small', level: 'h3', title: 'Nothing here yet' })
  assert.equal(kids(bare).length, 2, 'no body and no action draws neither')
  assert.equal(kids(bare)[1].nodeName.toLowerCase(), 'h3')
  assert.match(cls(bare), /empty-state-small/)
  assert.equal(find(emptyState({ object: 'page', title: 'T', body: 'B', bodyClass: 'tt-board-caption', titleId: 'x' }), 'tt-board-caption') !== null, true)
})

test('the drawing is theme-true: its stylesheet paints only with tokens, and only the sprout moves', async () => {
  const css = await readFile(new URL('../public/empty-state.css', import.meta.url), 'utf8')
  const js = await readFile(new URL('../public/empty-state.js', import.meta.url), 'utf8')
  for (const [name, source] of [['empty-state.css', css], ['empty-state.js', js]]) {
    assert.doesNotMatch(source.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''), /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(|oklch\(/i, `${name}: no literal colour`)
  }
  for (const m of css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/(?:fill|stroke|color|background):\s*([^;]+);/g)) assert.match(m[1], /^(var\(--[a-z0-9-]+\)|none|inherit)$/, `a paint is a token: ${m[0]}`)
  // Motion: two animations, both under .empty-state-sprout, both off under reduced motion.
  const rules = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const animated = [...rules.matchAll(/^([^{}@]+)\{[^}]*\banimation:\s*(?!none)/gm)].map(m => m[1].trim())
  assert.ok(animated.length >= 2 && animated.every(sel => sel.startsWith('.empty-state-sprout')), `only the sprout moves: ${animated.join(' | ')}`)
  const reduced = rules.slice(rules.indexOf('@media (prefers-reduced-motion: reduce)'))
  assert.match(reduced, /\.empty-state-sprout \.es-stem \{ animation: none; stroke-dashoffset: 0; \}/)
  assert.match(reduced, /\.empty-state-sprout \.es-leaf \{ animation: none; transform: none; \}/)
})

test('the Market’s old drawing is gone, and nothing draws a face, sparkle or confetti in an empty state', async () => {
  // The Market's drawn covers (a puzzle piece under twinkles) went on
  // 2026-11-18: a listing shows its cover, a theme itself, or its icon.
  await assert.rejects(readFile(new URL('../public/market-art.js', import.meta.url), 'utf8'), { code: 'ENOENT' })
  const market = await readFile(new URL('../public/page/market.js', import.meta.url), 'utf8')
  assert.doesNotMatch(market, /buildArt|coverArt|market-art|scene\(/)
  assert.match(market, /emptyState\(\{ object: none \? 'search' : 'sprout'/)
  const css = await readFile(new URL('../public/market.css', import.meta.url), 'utf8')
  assert.doesNotMatch(css, /market-build-art|market-bob|market-build-words|market-art|twinkle|market-scene/)
})
