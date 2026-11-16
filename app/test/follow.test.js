// The transcript follows new output while the reader is at its end
// (`public/follow.js`), asked twice: against a pane whose arithmetic is written
// here, everywhere, and against a real one under the page's own stylesheet in a
// Chromium, where `LOAM_CHROME` names one.
//
// The owner, 2026-10-07, on their phone: "If the chat is fully at the bottom… I
// would love for it to auto-scroll and move with the new information coming
// down." Every growth below is a thing that happens to a thread, and after each
// the question is the same one: is the view still at the end.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { follower, AT_BOTTOM_PX } from '../public/follow.js'
import { driven, chrome } from './layout.js'

// A scroller as a browser keeps one: `scrollTop` clamped to what there is to
// scroll, and the content and the window as two heights a test can change.
function pane({ content, window }) {
  let top = 0
  const p = {
    content,
    window,
    children: [],
    get clientHeight() {
      return p.window
    },
    get scrollHeight() {
      return Math.max(p.content, p.window)
    },
    get scrollTop() {
      return top
    },
    set scrollTop(v) {
      top = Math.max(0, Math.min(v, p.scrollHeight - p.window))
    },
  }
  return p
}

// A `ResizeObserver` the test calls: whatever changed size, the callback runs.
function observers() {
  const live = []
  class Observer {
    constructor(fn) {
      this.fn = fn
      live.push(this)
    }
    observe() {}
    disconnect() {}
  }
  return { Observer, resized: () => live.forEach((o) => o.fn([])) }
}

const atEnd = (p) => p.scrollHeight - p.scrollTop - p.clientHeight <= AT_BOTTOM_PX

// A draw, as `render()` does one: ask, change, put back.
function draw(f, p, change) {
  const top = p.scrollTop
  const following = f.following(p)
  change()
  if (following) f.pin(p)
  else p.scrollTop = top
  f.watch(p)
}

test('at the end, every growth of the thread keeps the view at the end', () => {
  const { Observer, resized } = observers()
  const f = follower({ Observer })
  // A phone's thread: 430 wide, the pane under the header and over the dock.
  const p = pane({ content: 2000, window: 700 })
  draw(f, p, () => {})
  f.pin(p)

  // New text arriving: a draw.
  draw(f, p, () => (p.content += 60))
  assert.ok(atEnd(p), 'new text')
  // A step added to a fold, or its count changing: a draw.
  draw(f, p, () => (p.content += 34))
  assert.ok(atEnd(p), 'a step')
  // A card appearing and the awaiting line: a draw each.
  draw(f, p, () => (p.content += 220))
  assert.ok(atEnd(p), 'a card')
  draw(f, p, () => (p.content += 28))
  assert.ok(atEnd(p), 'the awaiting line')
  // An image loading late: no draw, only a size changing.
  p.content += 300
  resized()
  assert.ok(atEnd(p), 'an image that loaded late')
  // Two growths before anybody asks: what the old rule lost, because the
  // second draw measured a bottom the first had already moved.
  p.content += 300
  draw(f, p, () => (p.content += 40))
  assert.ok(atEnd(p), 'a late image and then a delta, with no resize heard between')
})

test('a phone keyboard coming up and going down keeps the view at the end', () => {
  const { Observer, resized } = observers()
  const f = follower({ Observer })
  const p = pane({ content: 3000, window: 700 })
  f.pin(p)

  // Android lays the page out again before any script hears of the keyboard:
  // the pane is shorter under a `scrollTop` that has not moved, so measuring
  // "at the bottom?" now says no. The reader did not move; the end did.
  p.window = 380
  assert.ok(!atEnd(p))
  draw(f, p, () => {})
  assert.ok(atEnd(p), 'the keyboard came up and the next draw stopped following')

  // Send, and the keyboard goes down: the pane grows back and the browser
  // clamps. Heard as the pane resizing.
  f.send()
  draw(f, p, () => (p.content += 50))
  p.window = 700
  resized()
  assert.ok(atEnd(p), 'the keyboard closed after a send')
  draw(f, p, () => (p.content += 400))
  assert.ok(atEnd(p), 'the reply after the keyboard closed')
})

test('a send lands at the end from anywhere', () => {
  const f = follower({ Observer: null })
  const p = pane({ content: 3000, window: 700 })
  p.scrollTop = 900
  assert.ok(!f.following(p))
  f.send()
  draw(f, p, () => (p.content += 80))
  assert.ok(atEnd(p))
  // The send is one draw's worth: it does not hold the view down afterwards.
  p.scrollTop = 1000
  f.scrolled(p)
  draw(f, p, () => (p.content += 80))
  assert.equal(p.scrollTop, 1000)
})

test('scrolling up by any real amount stops following, and returning resumes it', () => {
  const { Observer, resized } = observers()
  const f = follower({ Observer })
  const p = pane({ content: 2000, window: 700 })
  f.pin(p)

  p.scrollTop -= 40
  f.scrolled(p)
  const there = p.scrollTop
  draw(f, p, () => (p.content += 60))
  assert.equal(p.scrollTop, there, 'a draw pulled a reader down who had scrolled up')
  p.content += 300
  resized()
  assert.equal(p.scrollTop, there, 'a late image pulled a reader down who had scrolled up')

  // A pixel is a browser's rounding, not a reader.
  p.scrollTop = p.scrollHeight
  f.scrolled(p)
  p.scrollTop -= 1
  draw(f, p, () => (p.content += 60))
  assert.ok(atEnd(p), 'within the tolerance is at the end')

  // Back at the end by hand, and a late growth follows again.
  p.scrollTop = p.scrollHeight
  f.scrolled(p)
  p.content += 100
  resized()
  assert.ok(atEnd(p))
})

// The same growths in a real layout, at a phone's width and a desktop's.
const SOURCE = readFileSync(new URL('../public/follow.js', import.meta.url), 'utf8').replace(/^export /gm, '')
const say = (n) => `<div class="line left"><div class="bubble"><div class="md"><p>${'A line of the reply, long enough to wrap at a phone width. '.repeat(n)}</p></div></div></div>`
const BODY = (height) =>
  `<div id="app" style="height:${height}px;display:flex;flex-direction:column"><section class="screen thread" style="flex:1;min-height:0;display:flex;flex-direction:column">` +
  `<div class="msgs" style="padding-bottom:120px">${say(6).repeat(12)}</div></section></div>`

async function realThread(document) {
  const frames = (n = 2) => new Promise((r) => { const go = () => (n-- <= 0 ? r() : requestAnimationFrame(go)); go() })
  const app = document.getElementById('app')
  const p = document.querySelector('.msgs')
  const f = follower()
  p.addEventListener('scroll', () => f.scrolled(p))
  const draw = (change) => {
    const top = p.scrollTop
    const following = f.following(p)
    change()
    if (following) f.pin(p)
    else p.scrollTop = top
    f.watch(p)
  }
  const end = () => p.scrollHeight - p.scrollTop - p.clientHeight <= 2
  const out = {}
  // The reply being written, which a fold's count and late growth land in.
  const reply = p.lastElementChild.querySelector('p')
  const last = () => reply
  draw(() => {})
  f.pin(p)
  await frames()
  draw(() => (last().textContent += ' And more words arriving as the model writes them.'.repeat(4)))
  await frames()
  out.text = end()
  // A fold's count: an existing child grows with no draw.
  last().insertAdjacentHTML('beforeend', '<span style="display:block;height:48px"></span>')
  await frames()
  out.fold = end()
  draw(() => p.insertAdjacentHTML('beforeend', '<div class="askcard" style="height:260px"></div>'))
  await frames()
  out.card = end()
  // An image whose size arrives after it is drawn.
  const img = document.createElement('div')
  draw(() => p.appendChild(img))
  await frames()
  img.style.height = '320px'
  await frames()
  out.image = end()
  // The keyboard: the app is the visual viewport, and it shrinks then grows.
  const tall = app.offsetHeight
  app.style.height = `${tall - 340}px`
  await frames()
  out.keyboardUp = end()
  f.send()
  draw(() => p.insertAdjacentHTML('beforeend', '<div class="line right"><div class="bubble">and this?</div></div>'))
  app.style.height = `${tall}px`
  await frames()
  out.keyboardDown = end()
  // Scrolled up: stays put through a growth.
  p.scrollTop -= 200
  await frames()
  const there = p.scrollTop
  draw(() => p.insertAdjacentHTML('beforeend', '<div style="height:90px"></div>'))
  last().insertAdjacentHTML('beforeend', '<span style="display:block;height:48px"></span>')
  await frames()
  out.heldUp = p.scrollTop === there
  // Back down by hand: follows again.
  p.scrollTop = p.scrollHeight
  await frames()
  last().insertAdjacentHTML('beforeend', '<span style="display:block;height:48px"></span>')
  await frames()
  out.resumed = end()
  return out
}

for (const [width, height] of [[430, 932], [1280, 800]]) {
  test(`in a real layout at ${width}px, the thread follows and lets go`, async (t) => {
    if (!chrome()) return t.skip('not painted: no Chromium named by LOAM_CHROME')
    const answer = await driven({ width, height, body: BODY(height), script: SOURCE }, realThread)
    assert.deepEqual(answer, {
      text: true,
      fold: true,
      card: true,
      image: true,
      keyboardUp: true,
      keyboardDown: true,
      heldUp: true,
      resumed: true,
    })
  })
}

test('an inactive chat adapter releases its observer and pending follow request', () => {
  const { Observer, resized } = observers(), f = follower({ Observer })
  const p = pane({ content: 2000, window: 700 })
  f.watch(p); f.pin(p)
  const top = p.scrollTop
  f.watch(null)
  p.content += 500
  resized()
  assert.equal(p.scrollTop, top, 'an old resize cannot scroll a hidden conversation')
  f.send(); f.watch(null)
  assert.equal(f.following(p), false, 'a queued follow does not cross adapters')
  f.watch(p); f.pin(p)
  p.content += 50; resized()
  assert.ok(atEnd(p), 'returning to the active adapter resumes normal following')
})
