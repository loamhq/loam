// What a redraw is allowed to do to the page.
//
// The page builds every screen from scratch on every change — one code path per
// row, which is the whole reason a full redraw was chosen — and `morph()` is
// what keeps that from being a page that replaces itself under a finger. A
// deployed build rebuilt the list on every stream tick, and an element taken
// from the page went stale within a second: two automated taps missed, and a
// thumb would have missed too.
//
// So the rules asserted here are the ones a finger cares about: a tick that
// changes nothing changes no node, a row that changes changes only itself, a
// row that moves moves rather than being rebuilt, and the node that stays keeps
// working — with the closure of the redraw that kept it, not the one that made
// it.
//
// Fixture-only, and worth saying plainly: this is a DOM written for the test
// (`test/fakedom.js`), not a browser. It proves the reconciler's arithmetic.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom, settle, sinceSettled, clearWrites, writes, body } from './fakedom.js'

installDom()
const { h, morph, kept, blob } = await import('../public/ui.js')

// A stand-in for the home screen's list: a header that never changes and a row
// per session, keyed the way `sessionRow` keys one.
function list(rows, { onTap = () => {} } = {}) {
  return h(
    'section',
    { class: 'screen home', key: 'screen:home' },
    h('div', { class: 'home-head' }, h('div', { class: 'display', text: 'Chats' })),
    h(
      'div',
      { class: 'recent' },
      ...rows.map((row) =>
        h(
          'button',
          { class: 'srow', key: row.id, onclick: () => onTap(row.id) },
          h('div', { class: 'srow-title', text: row.title }),
          h('div', { class: 'srow-status', text: row.status }),
        ),
      ),
    ),
  )
}

const THREE = [
  { id: 'a', title: 'The deploy that would not go', status: 'Done' },
  { id: 'b', title: 'Reading the week’s notes', status: 'Done' },
  { id: 'c', title: 'New chat', status: 'Working' },
]

function drawn(root, rows, options) {
  morph(root, [list(rows, options)])
  return [...root.firstChild.childNodes[1].childNodes]
}

test('a redraw that says the same thing does not touch the page', () => {
  const root = body()
  const before = drawn(root, THREE)
  settle(root)
  clearWrites()

  const after = drawn(root, THREE)

  assert.deepEqual(sinceSettled(), [], 'a snapshot that changed nothing wrote to the DOM')
  assert.equal(after[0], before[0])
  assert.equal(after[1], before[1])
  assert.equal(after[2], before[2])
})

test('a row that changed changes only itself, and stays the same element', () => {
  const root = body()
  const before = drawn(root, THREE)
  settle(root)
  clearWrites()

  const moved = THREE.map((row) => (row.id === 'b' ? { ...row, status: 'Needs your input' } : row))
  const after = drawn(root, moved)

  assert.equal(after[1], before[1], 'the row was rebuilt rather than updated')
  assert.equal(after[1].childNodes[1].textContent, 'Needs your input')
  const touched = sinceSettled()
  assert.equal(touched.length, 1, `one change, one write: ${JSON.stringify(touched.map((w) => w.what))}`)
  assert.equal(touched[0].what, 'text')
})

test('a row that moves up the list moves, and is not rebuilt around', () => {
  const root = body()
  const before = drawn(root, THREE)
  settle(root)
  clearWrites()

  const reordered = [THREE[2], THREE[0], THREE[1]]
  const after = drawn(root, reordered)

  assert.deepEqual(
    after.map((node) => node.childNodes[0].textContent),
    ['New chat', 'The deploy that would not go', 'Reading the week’s notes'],
  )
  assert.equal(after[0], before[2], 'the row that moved was rebuilt instead of moved')
  assert.equal(after[1], before[0])
  assert.equal(after[2], before[1])
})

test('a new row appears and an old one goes, and the rest are untouched', () => {
  const root = body()
  const before = drawn(root, THREE)
  settle(root)
  clearWrites()

  const changed = [{ id: 'd', title: 'Just started', status: 'Idle' }, THREE[0], THREE[2]]
  const after = drawn(root, changed)

  assert.equal(after.length, 3)
  assert.equal(after[1], before[0], 'a row that stayed was rebuilt')
  assert.equal(after[2], before[2])
  assert.equal(before[1].parentNode, null, 'the removed row is still in the tree')
})

test('the node that stays gets the fresh closure, not the one it was made with', () => {
  const root = body()
  const taps = []
  const before = drawn(root, THREE, { onTap: (id) => taps.push(`first:${id}`) })
  settle(root)
  clearWrites()

  const after = drawn(root, THREE, { onTap: (id) => taps.push(`second:${id}`) })
  assert.equal(after[0], before[0])
  after[0].fire('click')

  assert.deepEqual(taps, ['second:a'], 'the kept node answered to the redraw that made it')
})

const draftField = h('textarea', { id: 'draft', rows: '1' })

test('a screen with no keys keeps its nodes by position', () => {
  // The thread: a back button, a header and a composer, none of them a list.
  // A tick that changes nothing visible must leave all three where they are —
  // a real browser found references to exactly these going stale three times.
  const thread = (status) =>
    h(
      'section',
      { class: 'screen thread', key: 'screen:thread' },
      h('div', { class: 'thread-head' }, h('button', { class: 'iconbtn ghost', text: 'Back' }), h('div', { class: 'who', text: 'Scout' })),
      h('div', { class: 'msgs' }, h('div', { class: 'line left', key: 'l1' }, h('div', { class: 'bubble agent', text: 'hello' }))),
      h('div', { class: 'status', text: status }),
      h('form', { class: 'composer' }, kept(draftField)),
    )

  const root = body()
  morph(root, [thread('live')])
  const screen = root.firstChild
  const back = screen.childNodes[0].childNodes[0]
  const head = screen.childNodes[0]
  const composer = screen.childNodes[3]
  settle(root)
  clearWrites()

  morph(root, [thread('live')])
  assert.deepEqual(sinceSettled(), [])
  assert.equal(root.firstChild, screen)
  assert.equal(screen.childNodes[0].childNodes[0], back)
  assert.equal(screen.childNodes[0], head)
  assert.equal(screen.childNodes[3], composer)

  // And a change to one line leaves the other three alone.
  morph(root, [thread('quiet for 3 min')])
  assert.equal(screen.childNodes[0].childNodes[0], back)
  assert.equal(screen.childNodes[3], composer)
  assert.equal(screen.childNodes[2].textContent, 'quiet for 3 min')
})

test('another screen replaces this one, rather than being patched into it', () => {
  const root = body()
  morph(root, [h('section', { class: 'screen home', key: 'screen:home' }, h('div', { text: 'Chats' }))])
  const home = root.firstChild
  morph(root, [h('section', { class: 'screen thread', key: 'screen:thread' }, h('div', { text: 'Scout' }))])
  assert.notEqual(root.firstChild, home)
  assert.equal(home.parentNode, null)
})

test('a field a cursor lives in is handed back untouched', () => {
  // The composer is made once and handed to every redraw. Reconciling it would
  // be the one thing that must not happen: moving or rewriting the node a
  // person is typing into loses the caret and the selection.
  const draft = h('textarea', { id: 'draft', rows: '1' })
  const view = (has) =>
    h('form', { class: 'composer' }, kept(draft), has ? h('button', { class: 'roundsend', text: 'Send' }) : null)

  const root = body()
  morph(root, [view(false)])
  settle(root)
  clearWrites()

  // Nothing at all for a redraw that says the same thing, even though the
  // field is handed back by a function that is called again each time.
  morph(root, [view(false)])
  assert.deepEqual(sinceSettled(), [], 'handing the field back moved it')
  assert.equal(root.firstChild.childNodes[0], draft)

  morph(root, [view(true)])
  assert.equal(root.firstChild.childNodes[0], draft)
  assert.deepEqual(
    sinceSettled().map((w) => w.what),
    ['insert'],
    'the send button appearing rewrote more than itself',
  )
})

// ------------------------------------------------- a style the browser takes
//
// The deployed app logged one console error on every chat it opened: *"Applying
// inline style violates the following Content Security Policy directive
// 'default-src 'self''"*, at the line in `patch()` that copied attributes. This
// page is served under `default-src 'self'` with no `style-src`, so a style
// *attribute* is an inline style and the browser refuses it: the declarations
// never land and the element goes on drawing what it was drawing. Every kept
// node whose style changed was therefore stale on the deployed app — a ring
// that follows a session's status, a blob's colour, a quota bar's width, the
// transcript's padding when it changes through the tree.
//
// Nothing here could catch it, because the fixture had no policy in it. It has
// one now — `test/fakedom.js` refuses a style attribute the way the browser
// does — so these four say what the reconciler may do with a style, and the
// fixture underneath them is what makes the whole suite fail if the blocked
// mechanism comes back anywhere.

const styleWrites = (node) => writes.filter((w) => w.node === node && (w.what === 'style' || w.what === 'style-gone'))
const attrWrites = (node) => writes.filter((w) => w.node === node && w.what === 'attr')

test('a style that changed is carried across property by property', () => {
  const root = body()
  const bar = (width, colour) => h('div', { class: 'meter-fill', style: { width, background: colour } })

  morph(root, [bar('10%', 'red')])
  const node = root.firstChild
  settle(root)
  clearWrites()

  morph(root, [bar('60%', 'red')])
  assert.equal(root.firstChild, node, 'the bar was rebuilt rather than restyled')
  assert.equal(node.style.width, '60%', 'the width the redraw asked for did not land')
  assert.equal(node.style.background, 'red', 'a property nothing changed was disturbed')
  // One declaration changed, one write — and not one of them an attribute.
  assert.deepEqual(styleWrites(node).map((w) => [w.name, w.value]), [['width', '60%']])
  assert.deepEqual(attrWrites(node), [], 'a style reached the node as an attribute')
})

test('a style the fresh node does not name is taken off, and nothing else with it', () => {
  const root = body()
  const blob = (ring) =>
    h('div', { class: 'blob', style: { borderRadius: '58% 42%', ...(ring ? { boxShadow: '0 0 0 3px red' } : {}) } })

  morph(root, [blob(true)])
  const node = root.firstChild
  assert.match(node.getAttribute('style'), /box-shadow/)
  settle(root)
  clearWrites()

  morph(root, [blob(false)])
  assert.equal(node.style.boxShadow, '', 'the ring stayed on a node that no longer draws one')
  assert.equal(node.style.borderRadius, '58% 42%', 'the shape went with the ring')
  assert.deepEqual(styleWrites(node).map((w) => [w.what, w.name]), [['style-gone', 'box-shadow']])
  assert.deepEqual(attrWrites(node), [])
})

test('a redraw that says the same style writes nothing at all', () => {
  const root = body()
  const pane = () => h('div', { class: 'msgs', style: { paddingBottom: '106px' } })

  morph(root, [pane()])
  settle(root)
  clearWrites()

  morph(root, [pane()])
  assert.deepEqual(sinceSettled(), [], 'a padding that did not change was written again')
  assert.equal(root.firstChild.style.paddingBottom, '106px')
})

// --------------------------------------- a colour that is a custom property
//
// The owner, 2026-09-25: "the agent icons are acting weird right now and the
// icons shapes keep disappearing". Measured in a browser on the deployed app:
// a blob in the agents strip carried `width`, `height`, `font-size` and
// `border-radius` on its style and **no background at all**, computing
// transparent — so only the faint letter showed and the blob read as a shape
// that had vanished.
//
// Every agent colour on this page is `var(--persona-olive)` and its ten neighbours, and a
// shorthand set to a value holding `var()` is a *pending-substitution* value: a
// browser keeps the shorthand's longhands, enumerates all nine of them, and
// answers `''` for every one. The reconciler read the declarations back through
// exactly those calls, so a colour that changed never landed and a colour that
// had not changed was taken off as "a property the fresh node does not name".
// `border-radius` holds no `var()`, which is why the shape stayed and the
// colour went — the whole of what the owner was looking at.
//
// These two fail against that reconciler and pass against the one that diffs
// the declarations the page asked for. The fixture is what gives them teeth: it
// models the pending-substitution expansion now, which it did not before.

const LOOK = { color: 'var(--persona-olive)', shape: '40% 60% 60% 40% / 68% 32%', initial: 'Cl' }

test('an agent blob keeps its colour when the row around it redraws', () => {
  const root = body()
  const row = (status) =>
    h(
      'div',
      { class: 'srow', key: 'a' },
      blob(LOOK, { key: 'blob', size: 50, font: 21 }),
      h('div', { class: 'srow-status', text: status }),
    )

  morph(root, [row('Working')])
  const node = root.firstChild.firstChild
  assert.equal(node.style.background, 'var(--persona-olive)', 'the blob was drawn without its colour')

  // A snapshot lands and the row says something else: the one thing that
  // happens on this page every few seconds.
  morph(root, [row('Done')])
  assert.equal(root.firstChild.firstChild, node, 'the blob was rebuilt rather than kept')
  assert.equal(node.style.background, 'var(--persona-olive)', 'the redraw wiped the agent’s colour')
  assert.equal(node.style.borderRadius, LOOK.shape, 'the redraw wiped the agent’s shape')
  assert.match(node.getAttribute('style'), /background: var\(--persona-olive\)/)

  // And it survives being redrawn again, and again — a chat open for an hour is
  // hundreds of these.
  for (const status of ['Working', 'Needs your input', 'Done']) morph(root, [row(status)])
  assert.equal(node.style.background, 'var(--persona-olive)', 'the colour went on a later redraw')
})

test('an agent blob whose colour changes takes the new one', () => {
  const root = body()
  const avatar = (colour) => h('div', { class: 'row' }, blob({ ...LOOK, color: colour }, { key: 'blob', size: 40 }))

  morph(root, [avatar('var(--persona-olive)')])
  const node = root.firstChild.firstChild
  settle(root)
  clearWrites()

  morph(root, [avatar('var(--persona-teal)')])
  assert.equal(root.firstChild.firstChild, node, 'the blob was rebuilt rather than restyled')
  assert.equal(node.style.background, 'var(--persona-teal)', 'a colour written as a custom property never landed')
  assert.deepEqual(styleWrites(node).map((w) => [w.name, w.value]), [['background', 'var(--persona-teal)']])
  assert.deepEqual(attrWrites(node), [], 'a style reached the node as an attribute')
})

// The guard itself, stated so that it cannot be quietly removed: the fixture
// refuses the two mechanisms the policy refuses. A page that goes back to
// `setAttribute('style', ...)`, to a `style="..."` in an HTML string, or to a
// `<style>` element it builds, fails here and in every test that draws a
// screen — rather than on the owner's phone.
test('the fixture refuses a style attribute and an inline style element, as the browser does', () => {
  const node = body()
  assert.throws(() => node.setAttribute('style', 'padding-bottom: 106px'), /content security policy/)
  assert.throws(() => document.createElement('style'), /content security policy/)
  assert.throws(() => h('div', { style: 'padding-bottom: 106px' }), /content security policy/)
})
