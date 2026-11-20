// The stylesheet, read back.
//
// **This is the only test in the suite that is about CSS, and it exists for one
// defect.** After tapping pin inside a thread the button showed the wrong
// colours until the thread was closed and reopened. `test/app.test.js` asks the
// page's half of that question and answers it: the button's `class` attribute
// after a pin is exactly the attribute it has when a pinned thread is opened
// fresh, so neither the reconciler nor the header render is at fault. What is
// left is the stylesheet, and what it was doing is this:
//
// A touch screen has no pointer, so a browser on a phone fakes one — a tap
// leaves `:hover` stuck on whatever it landed on until something else is
// tapped. `.iconbtn.ghost:hover` is one class more specific than `.iconbtn.on`,
// so the button a finger had just pinned kept the ghost's near-transparent
// hover fill under the on state's near-white glyph, and only lost it when the
// finger's hover moved to the back button on the way out of the thread.
//
// So the rule the fix rests on is: **every `:hover` in this app is inside
// `@media (hover: hover)`**, and touch gets `:active`, which is what the design
// bundle says it should get ("Hover/pressed (touch highlight): rows tint --paper-hover-alpha /
// --paper-pressed"). That is a property of the file, which is a thing a test can hold.
//
// **Fixture-only, and plainly so**: this reads the stylesheet, it does not
// paint it. There is no browser on the box this project is built on. What it
// can prove is that the rule the fix is made of has not quietly come undone the
// next time a hover style is added, which is the failure this was written
// against.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Read aloud and Dictation are Market plugins since 0.2.9; their listed copies' styles are read beside the core's.
const CSS = ['../public/style.css', '../../plugins/_listed/read-aloud/page/style.css', '../../plugins/_listed/dictation/page/style.css'].map(file => readFileSync(new URL(file, import.meta.url), 'utf8')).join('\n')

// Every selector in the file, with whether it is inside an `@media (hover:
// hover)` block. Comments come off first, because this file explains itself at
// length and the words `:hover` are in the explanation.
function selectors(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = []
  const guard = [] // one entry per open block: is it a (hover: hover) media query
  let head = ''
  for (const ch of clean) {
    if (ch === '{') {
      const selector = head.trim().replace(/\s+/g, ' ')
      const isHoverMedia = /^@media[^{]*\(\s*hover\s*:\s*hover\s*\)/.test(selector)
      out.push({ selector, guarded: guard.some(Boolean) })
      guard.push(isHoverMedia)
      head = ''
    } else if (ch === '}') {
      guard.pop()
      head = ''
    } else {
      head += ch
    }
  }
  return out
}

const RULES = selectors(CSS)

test('every hover style is inside @media (hover: hover), so a tap cannot stick one on', () => {
  const hovers = RULES.filter((rule) => rule.selector.includes(':hover'))
  // Not a vacuous pass: the app does have hover styles, and they are the
  // design's.
  assert.equal(hovers.length > 10, true, `only ${hovers.length} hover rules were found at all`)
  assert.deepEqual(
    hovers.filter((rule) => !rule.guarded).map((rule) => rule.selector),
    [],
    'a hover style outside @media (hover: hover) is a style a tap leaves stuck on a phone',
  )
})

test('the touch highlight is :active, and it is not hidden behind a pointer', () => {
  const actives = RULES.filter((rule) => rule.selector.includes(':active'))
  assert.equal(actives.length > 0, true)
  assert.deepEqual(
    actives.filter((rule) => rule.guarded).map((rule) => rule.selector),
    [],
    'the design’s pressed state was put behind a pointer a phone does not have',
  )
})

test('the pinned icon button outranks the ghost’s hover, for a real pointer too', () => {
  // `.iconbtn.on` alone is (0,2,0) and `.iconbtn.ghost:hover` is (0,3,0), so on
  // a laptop with a mouse the on state would lose under the cursor even with
  // the media query in place. Both selectors are the fix; one is not.
  const on = RULES.find((rule) => rule.selector.includes('.iconbtn.ghost.on') && !rule.selector.includes(':hover'))
  assert.notEqual(on, undefined, 'there is no .iconbtn.ghost.on rule to beat .iconbtn.ghost:hover')
  const ghostHover = RULES.findIndex((rule) => rule.selector.includes('.iconbtn.ghost:hover'))
  assert.equal(RULES.indexOf(on) > ghostHover, true, 'the on state is declared before the hover it has to beat')
})

test('the open chat’s row uses its avatar hue and a proportional right-edge bar', () => {
  const open = /\.srow\.on\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(open, null, 'there is no treatment for the row the pane is reading from')
  assert.match(open[1], /background:.*var\(--agent-color\)/)
  assert.doesNotMatch(open[1], /box-shadow|outline|border:/)
  assert.doesNotMatch(CSS, /--row-selected|--row-lift/)
  const bar = /\.srow::before\s*\{([^}]*)\}/.exec(CSS)[1]
  assert.match(bar, /background:\s*var\(--agent-color\)/)
  assert.match(bar, /top:\s*50%/)
  assert.match(bar, /translateY\(-50%\)/)
  assert.match(bar, /right:\s*0/)
  assert.match(bar, /height:\s*var\(--selection-height, 35%\)/)
  assert.match(bar, /transition:\s*height 180ms ease-out, opacity 180ms ease-out/)
  assert.match(bar, /border-radius:\s*999px/)
})

test('the rail’s buttons are rounded squares, not circles', () => {
  for (const [cls, radius] of [['railtab', '18px'], ['railround', '16px']]) {
    const rule = new RegExp(`\\.${cls}\\s*\\{([^}]*)\\}`).exec(CSS)
    assert.notEqual(rule, null)
    assert.match(rule[1], new RegExp(`border-radius:\\s*${radius}`), `.${cls} is not a rounded square`)
  }
})

// Which of a row's two lines is the big one. `test/app.test.js` asserts the
// order the two are drawn in and cannot see a font size; this is the other
// half, and the two together are the whole of "the title is the title".
test('a row’s chat name is the 17px slot and the agent’s name is the muted one', () => {
  const title = /\.srow-title\s*\{([^}]*)\}/.exec(CSS)
  const name = /\.srow-name\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(title, null)
  assert.notEqual(name, null)
  assert.match(title[1], /font-weight:\s*700/)
  assert.match(title[1], /font-size:\s*17px/)
  assert.match(name[1], /font-size:\s*14\.5px/)
  assert.match(name[1], /color:\s*var\(--paper-ink-3\)/)
  assert.doesNotMatch(name[1], /font-weight/, 'the agent’s name is still being drawn as the row’s heading')
  // Both are one line with an ellipsis: the design's slots did not change, only
  // which name is drawn in each.
  for (const rule of [title[1], name[1]]) assert.match(rule, /text-overflow:\s*ellipsis/)
  // And the row's secondary line is the one that has to shrink, since the
  // "still running" tag sits beside it.
  assert.match(CSS, /\.srow-line \.srow-name\s*\{[^}]*min-width:\s*0/)
})

// A table is drawn by nodes `test/markdown.test.js` asserts and by four rules
// nothing but this file can see — the padding, the rule between the rows, the
// header's own fill, and the scroller that keeps a wide one inside its bubble.
test('a table is padded, ruled between its rows, and scrolls inside its bubble', () => {
  const wrap = /\.md \.tablewrap\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(wrap, null, 'a table has no scroller of its own, so a wide one pushes the thread sideways')
  assert.match(wrap[1], /overflow-x:\s*auto/)
  assert.match(wrap[1], /max-width:\s*100%/)

  // Wider than the scroller when its columns need it, and never narrower than
  // it when they do not.
  const table = /\.md table\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(table, null)
  assert.match(table[1], /width:\s*max-content/)
  assert.match(table[1], /min-width:\s*100%/)
  // The bubble is `pre-wrap`; a cell that inherited it would be one line
  // however long it is, and the scroller would be the whole table's width.
  assert.match(table[1], /white-space:\s*normal/)

  const cells = /\.md th, \.md td\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(cells, null, 'the cells have no padding rule')
  assert.match(cells[1], /padding:\s*\d/)

  // The header row is told apart by a fill, and the rows by one hairline in the
  // colour the rest of `.md` rules in — the same token as the `hr`.
  assert.match(/\.md thead th\s*\{([^}]*)\}/.exec(CSS)[1], /background:\s*var\(--paper-pane\)/)
  const rule = /\.md tbody tr \+ tr td[^{]*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(rule, null, 'there is no rule between a table’s rows')
  assert.match(rule[1], /border-top:\s*1px solid var\(--paper-rule-strong\)/)
  assert.match(/\.md hr\s*\{([^}]*)\}/.exec(CSS)[1], /var\(--paper-rule-strong\)/, 'the two rules in a reply are drawn in different colours')

  // What the parser's alignment classes are for. Right is the one the numbers
  // get, and it has to exist for the column to move at all.
  assert.match(CSS, /\.md td\.ta-right\s*\{[^}]*text-align:\s*right/)
  assert.match(CSS, /\.md td\.ta-center\s*\{[^}]*text-align:\s*center/)
  // The person's bubble is the accent fill, so its header and its rules are the
  // bubble's own ink rather than a surface colour that would vanish into it.
  assert.match(CSS, /\.say\.user\.md thead th\s*\{[^}]*currentColor/)
})

test('the composer clears the phone’s own strip, and the app can be sized to the visual viewport', () => {
  // The design's 14px under the composer plus whatever the device keeps for
  // itself. The owner: "the input box doesn't have enough space at the bottom."
  assert.match(CSS, /\.composer\s*\{[^}]*padding:\s*6px 12px calc\(14px \+ env\(safe-area-inset-bottom/)
  // And the fallback the page overwrites from `window.visualViewport` stays,
  // for a browser that has none to read.
  assert.match(CSS, /#app\s*\{[^}]*height:\s*calc\(100dvh \/ var\(--ui-scale, 1\)\)/)
})

test('the composer is a rounded rectangle of two rows, and not a pill', () => {
  // The owner, 2026-09-22: "The box design should be a rectangle with rounded
  // edges, not half circles." 999px on this box is the defect by name, and the
  // radius is the design's own card radius.
  const box = /\.composer-box\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(box, null, 'there is no .composer-box rule at all')
  const radius = /border-radius:\s*(\d+)px/.exec(box[1])
  assert.notEqual(radius, null, 'the box has no radius of its own')
  assert.equal(Number(radius[1]) >= 16 && Number(radius[1]) <= 18, true, `the box radius is ${radius[1]}px`)
  // Two rows stacked, not one line of things side by side.
  assert.match(box[1], /flex-direction:\s*column/)
  // And the row of actions under it, which is `.composer-actions` — see the
  // two tests below for why the name matters.
  assert.match(CSS, /\.composer-actions\s*\{[^}]*display:\s*flex/)
})

// -------------------------------------------- row two of the composer is a row
//
// Seen in a real browser at 390px, 2026-09-22: the +, the mic and the send
// stacked vertically in a block about 150px tall under the textarea, and the
// composer filled a third of the screen.
//
// The cause was not a media query. Row two was `.actions`, which is also the
// class the action sheet gives its card — `.actions { … flex-direction: column
// … }`, four hundred lines further down the same file — so the later rule won
// and row two was drawn as the sheet's column. The fix is the name, so what
// these hold is the name: that row two has a class of its own, that nothing
// gives that class a column, and that no width query anywhere is allowed to.

// Every rule with its declarations and the media queries it sits inside.
// `selectors()` above answers where a rule is; this answers what it says.
function rules(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = []
  const media = []
  let head = ''
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i]
    if (ch === '{') {
      const selector = head.trim().replace(/\s+/g, ' ')
      head = ''
      if (selector.startsWith('@')) {
        media.push(selector)
        continue
      }
      let body = ''
      i += 1
      while (i < clean.length && clean[i] !== '}') {
        body += clean[i]
        i += 1
      }
      out.push({ selector, body, media: [...media] })
    } else if (ch === '}') {
      media.pop()
      head = ''
    } else {
      head += ch
    }
  }
  return out
}

const ROW = 'composer-actions'
const RULE_LIST = rules(CSS)
const rowRules = RULE_LIST.filter((rule) => rule.selector.includes(`.${ROW}`))

test('the composer’s row two is a row, and no rule in the file turns it into a column', () => {
  const own = rowRules.find((rule) => rule.selector === `.${ROW}`)
  assert.notEqual(own, undefined, `there is no .${ROW} rule: row two has no class of its own again`)
  assert.match(own.body, /display:\s*flex/)
  // Written out rather than left to the default, because the default is what
  // the collision overrode and a reader of this rule should see which way it
  // goes without knowing that story.
  assert.match(own.body, /flex-direction:\s*row/)
  assert.deepEqual(
    rowRules.filter((rule) => /flex-direction:\s*column/.test(rule.body)).map((rule) => rule.selector),
    [],
    'something in this file draws row two of the composer as a column',
  )
  // And the class it was taken out of is still the sheet's, still a column, so
  // the two are not one rule apart from each other by accident.
  const sheet = RULE_LIST.find((rule) => rule.selector === '.actions')
  assert.notEqual(sheet, undefined)
  assert.match(sheet.body, /flex-direction:\s*column/)
})

test('no width query sets a column direction on row two, at any width', () => {
  // The brief's own check: the phone gets the same row the desktop does, so a
  // `max-width` rule that stacks it would be the defect coming back by the
  // route it was first looked for down.
  const narrow = RULE_LIST.filter(
    (rule) => rule.media.some((query) => /max-width/.test(query)) && /flex-direction:\s*column/.test(rule.body),
  )
  assert.deepEqual(
    narrow.filter((rule) => rule.selector.includes(`.${ROW}`) || rule.selector.includes('.composer-box')).map((r) => r.selector),
    [],
    'a max-width media query stacks the composer’s buttons',
  )
  // Nothing in the file may narrow row two at any width, media query or not.
  assert.deepEqual(
    RULE_LIST.filter((rule) => rule.media.length > 0 && rule.selector.includes(`.${ROW}`)).map((rule) => ({
      at: rule.media.join(' '),
      selector: rule.selector,
    })),
    [],
    'row two is styled differently at some widths, which is the thing that went wrong',
  )
})

test('the buttons in row two are the design’s 40px targets', () => {
  for (const button of ['plusbtn', 'roundmic', 'roundsend']) {
    const rule = RULE_LIST.find((r) => r.selector === `.${button}`)
    assert.notEqual(rule, undefined, `there is no .${button} rule`)
    assert.match(rule.body, /width:\s*40px/, `.${button} is not 40px wide`)
    assert.match(rule.body, /height:\s*40px/, `.${button} is not 40px tall`)
  }
  // The model button is the one that carries a name, so it is as tall as the
  // round ones and as wide as its label — and it gives way rather than pushing
  // the send off the end of the row, which is the `min-width` and the ellipsis.
  const model = RULE_LIST.find((r) => r.selector === '.modelbtn')
  assert.notEqual(model, undefined, 'there is no .modelbtn rule')
  assert.match(model.body, /height:\s*40px/, '.modelbtn is not 40px tall')
  assert.match(model.body, /min-width:\s*0/, '.modelbtn cannot shrink, so a long model name pushes the send out')
  const name = RULE_LIST.find((r) => r.selector === '.modelbtn-name')
  assert.notEqual(name, undefined, 'there is no .modelbtn-name rule')
  assert.match(name.body, /text-overflow:\s*ellipsis/)
})

test('the six lines the draft grows to are one number, in both of its homes', () => {
  // The height is written by `app.js` and clamped by the stylesheet, and a box
  // grown past what CSS will draw is a box that clips what is being typed. So
  // the two numbers are held together here rather than hoped to agree.
  const css = /\.composer-box\s+\.draft\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(css, null, 'there is no rule for the composer’s field')
  const capped = /max-height:\s*(\d+)px/.exec(css[1])
  assert.notEqual(capped, null, 'the draft has no ceiling, so it grows over the transcript')
  const APP = readFileSync(new URL('../public/page/composer.js', import.meta.url), 'utf8')
  const inApp = /DRAFT_MAX_PX\s*=\s*(\d+)/.exec(APP)
  assert.notEqual(inApp, null, 'app.js no longer names the height it clamps to')
  assert.equal(capped[1], inApp[1], 'the stylesheet and the page disagree about how tall the draft may grow')

  // Six lines of the design's 16px type: the ceiling is what six lines of it
  // measure, give or take the box's own padding, and not a number from nowhere.
  const size = Number(/font-size:\s*(\d+(?:\.\d+)?)px/.exec(css[1])[1])
  const lead = Number(/line-height:\s*(\d+(?:\.\d+)?)/.exec(css[1])[1])
  const six = size * lead * 6
  assert.equal(Math.abs(Number(capped[1]) - six) < 12, true, `${capped[1]}px is not six lines of ${size}px/${lead}`)
})

test('the scrolling of a long draft is the field’s, not the screen’s', () => {
  // With the keyboard up there is no room to grow into, so row one scrolls
  // inside the box and the box stays where it is above the keyboard.
  const css = /\.composer-box\s+\.draft\s*\{([^}]*)\}/.exec(CSS)[1]
  assert.match(css, /overflow-y:\s*auto/)
  // And the composer never gives its height away to the transcript: it is the
  // transcript that shrinks.
  assert.match(/\.composer\s*\{([^}]*)\}/.exec(CSS)[1], /flex:\s*none/)
})

// ------------------------------------- the list column closes, it does not go
//
// The owner, 2026-09-22: "The collapsing of the middle column should happen with
// an elegant animation." `app.js` now keeps the column in the tree and marks it
// closed — `test/desktop.test.js` holds that — and the movement itself is here,
// because a transition is a property of the stylesheet and of nothing else.
//
// **Fixture-only in the strongest sense**: this reads the file, it does not
// watch anything move. What it can hold is that the two declarations the
// movement is made of are still in it, and that the setting that turns motion
// off still reaches them.

test('hiding the list column is a transition, and the pane grows into the space', () => {
  const column = /\.listcol\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(column, null, 'there is no .listcol rule at all')
  const transition = /transition:\s*([^;]+);/.exec(column[1])
  assert.notEqual(transition, null, 'the column closes with no transition, which is a jump')
  // Width is what the pane grows into; opacity is what stops the rows from
  // being read while they are 40px wide.
  assert.match(transition[1], /width\s+\d+m?s/)
  assert.match(transition[1], /opacity\s+\d+m?s/)
  // About a fifth of a second, which is the brief's number and the design's
  // sense of it: long enough to be seen, short enough not to be waited for.
  const ms = [...transition[1].matchAll(/(\d+)ms/g)].map((m) => Number(m[1]))
  assert.equal(ms.every((n) => n >= 120 && n <= 320), true, `the column moves over ${ms.join(' and ')}ms`)
  // The closed state is a class on the same element, which is the only thing a
  // transition can run between.
  assert.match(CSS, /\.listcol\.away\s*\{[^}]*width:\s*0/)
  assert.match(CSS, /\.listcol\.away\s*\{[^}]*opacity:\s*0/)
  // And the way back in arrives after the column has gone rather than over it.
  const showbar = /\.showbar\s*\{([^}]*)\}/.exec(CSS)
  assert.match(showbar[1], /animation:\s*loamFadeIn/)
})

test('a person who asked for less motion gets no transition either', () => {
  const block = /@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/.exec(CSS)
  assert.notEqual(block, null, 'the stylesheet no longer answers prefers-reduced-motion at all')
  // Animations were already answered; the column's transition is the line this
  // grew, and a movement the setting does not reach is the whole failure.
  assert.match(block[1], /animation-duration:\s*\.001ms\s*!important/)
  assert.match(block[1], /transition-duration:\s*\.001ms\s*!important/)
})

// The turn line, the background chip and the sign-in warning are all new
// classes, and every one of them is a size or a colour the page itself cannot
// see. `test/fakedom.js` has no stylesheet under it, so this file is the only
// one that can hold these.

test('the line under a turn is the design’s quiet 11px, with no border to it', () => {
  // The owner, 2026-09-22: "I want the design to be less visible and in my face
  // and more available easily if I want it." One muted token, no fill, no
  // border, and the ink is the muted one the rest of the thread's asides use.
  const turn = /\n\.turn\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(turn, null, 'there is no rule for the line under a turn')
  assert.match(turn[1], /font-size:\s*11px/)
  assert.match(turn[1], /color:\s*var\(--paper-ink-3\)/)
  assert.match(turn[1], /border:\s*0/, 'a button that looks like a button is not a quiet token')
  assert.match(turn[1], /background:\s*transparent/)
})

// The owner, 2026-09-25, on the pill the count used to sit in and the card behind
// it: it was cramped, and a background task's description is a whole sentence.
// So the pill is gone — the quiet line above the composer *is* the control — and
// what it opens has room for the sentence.
test('nothing in the background list is truncated, and the pill it hung off is gone', () => {
  assert.doesNotMatch(CSS, /\.bgchip\s*[,{]/, 'the dark pill is back in the stylesheet')
  assert.doesNotMatch(CSS, /\.bgwrap\s*[,{]/, 'the pill’s wrapper is back in the stylesheet')
  // Its popover is the same card the composer's menus are drawn in, so there is
  // one popover in this app and not two.
  const pop = /\.bgpop\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(pop, null, 'there is no rule for the background list’s popover')
  assert.doesNotMatch(pop[1], /left:|right:|bottom:|top:/, 'the list has its own positioning again')
  // Every row wraps rather than being cut: the description is the whole of what
  // a task says.
  const row = /\.bgtask-what\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(row, null, 'there is no rule for a task’s own words')
  assert.match(row[1], /white-space:\s*normal/)
  assert.doesNotMatch(row[1], /text-overflow|nowrap/, 'a task’s description is truncated')
})

// The transcript runs behind the composer rather than stopping above it.
// The owner, 2026-09-25: "having the chat when scrolled through feel like it is
// going behind the input box area without that gap on top of the box."
test('the dock floats on the frosted ground and the transcript is padded past it', () => {
  const dock = /\.thread-dock\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(dock, null, 'there is no rule for the dock')
  assert.match(dock[1], /position:\s*absolute/, 'the dock is still in the column')
  assert.match(dock[1], /bottom:\s*0/)
  assert.match(dock[1], /background:\s*var\(--paper-page-glass\)/, 'the dock is not on the design’s frosted fill')
  assert.match(dock[1], /backdrop-filter:\s*blur/, 'the frosted ground is not frosted')

  // The composer inside it has no fill of its own: an opaque box over the blur
  // would be a hard rectangle where the design has a frosted edge.
  const composer = /\n\.composer\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(composer, null)
  assert.match(composer[1], /background:\s*transparent/, 'the composer paints over the dock it sits on')

  // And the scroller clears it. The real figure is measured at run time by
  // `fitDock()`; this is the fallback that stands until the first measurement,
  // and a zero here is a last message nobody can scroll to.
  const msgs = /\n\.msgs\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(msgs, null)
  const pad = /padding:\s*[\d.]+px\s+[\d.]+px\s+([\d.]+)px/.exec(msgs[1])
  assert.notEqual(pad, null, 'the transcript has no bottom padding to clear the dock')
  assert.equal(Number(pad[1]) >= 80, true, `the transcript clears the dock by ${pad[1]}px, which is less than a composer`)
})

// The notice row inside the composer, in its three tones. The wake-cost and
// working lines are deliberately not the error colour — neither is a problem —
// and the row is the muted 12.5px the mock gives it, left to right with a dot.
test('the notice row is muted 12.5px, left to right, and only a failure takes the error colour', () => {
  const row = /\n\.noticerow\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(row, null, 'there is no rule for the notice row')
  assert.match(row[1], /font-size:\s*12\.5px/)
  assert.match(row[1], /color:\s*var\(--paper-ink-3\)/)
  assert.match(row[1], /display:\s*flex/)
  assert.doesNotMatch(row[1], /text-align:\s*center/, 'the row is centred like the strip it replaced')
  assert.doesNotMatch(row[1], /--danger/, 'every notice is drawn as an error')
  assert.match(CSS, /\.noticerow\.warn\s*\{[^}]*var\(--warning\)/)
  assert.match(CSS, /\.noticerow\.bad\s*\{[^}]*var\(--danger\)/)
  const dot = /\n\.noticerow-dot\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(dot, null)
  assert.match(dot[1], /width:\s*7px/)
  assert.match(dot[1], /box-shadow:\s*0 0 0 3px/)
  // The strip above the box is gone, class and all.
  assert.doesNotMatch(CSS, /\.quietline/)
  // It grows in and shrinks out over the 160ms app.js keeps a leaving row for.
  assert.match(row[1], /animation:\s*loamNoticeIn 160ms/)
  assert.match(CSS, /\.noticerow\.leaving\s*\{[^}]*animation:\s*loamNoticeOut 160ms/)
})

// The context fill along the box's top edge: 3px, clipped by the box's radius
// in a layer of its own, and the ring's amber and red at its two levels.
test('the context fill is a 3px track clipped by the box, amber and red at its levels', () => {
  assert.match(CSS, /\n\.composer-box\s*\{[^}]*position:\s*relative/)
  const clip = /\n\.ctxfill\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(clip, null)
  assert.match(clip[1], /overflow:\s*hidden/)
  assert.match(clip[1], /border-radius:/)
  assert.match(CSS, /\n\.ctxfill-track\s*\{[^}]*height:\s*3px/)
  assert.match(CSS, /\.ctxfill\.warn \.ctxfill-bar\s*\{[^}]*var\(--warning\)/)
  assert.match(CSS, /\.ctxfill\.high \.ctxfill-bar\s*\{[^}]*var\(--danger\)/)
})

// The draft, full screen. The one thing only the stylesheet can answer about it
// is that the six-line ceiling row one lives under is lifted here — that ceiling
// is the whole reason this screen exists.
test('the full-screen editor covers the page and has no line ceiling', () => {
  const editor = /\n\.editor\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(editor, null, 'there is no rule for the full-screen editor')
  assert.match(editor[1], /position:\s*absolute/)
  assert.match(editor[1], /inset:\s*0/)
  const field = /\.editor-draft\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(field, null, 'there is no rule for the editor’s field')
  assert.match(field[1], /max-height:\s*none/, 'the big editor kept the composer’s six-line ceiling')
})

// The owner, 2026-09-25: "Maybe just have a small and discrete indicator that the
// dropdown includes a failure. It shouldn't be too attracting. It should be a
// very very discrete thing since it is usually not a big deal." So what the
// header adds after the count is the header's own muted ink and normal weight —
// **not** `--danger`, which is this app's error colour and is what the row inside
// the fold carries. The page's half of this is in `test/app.test.js`: no icon,
// no colours of its own, and the failure still in the fold. This is the half
// only the stylesheet can answer.
test('the fold’s failure marker is muted type and never the error colour', () => {
  const failed = /\.fold-head\s+\.failed\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(failed, null, 'there is no rule for the fold’s failure marker')
  assert.match(failed[1], /color:\s*var\(--paper-ink-3\)/, 'the marker is not drawn in the muted ink the rest of the asides use')
  assert.match(failed[1], /font-weight:\s*400/, 'the marker is heavier than the words beside it')
  assert.doesNotMatch(failed[1], /--danger/, 'the quiet marker is drawn in the error colour')
  assert.doesNotMatch(failed[1], /background|border/, 'the quiet marker has a fill or a border')

  // And the live line the header reads while the work is going on is one line,
  // clipped where the card ends — a paragraph of the agent's thinking must not
  // make the fold three rows tall.
  const live = /\.fold-head\s+b\.live\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(live, null, 'there is no rule for the line a working fold reads')
  const head = /\.fold-head\s+b\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(head, null)
  assert.match(head[1], /white-space:\s*nowrap/, 'the header’s line can wrap')
  assert.match(head[1], /text-overflow:\s*ellipsis/, 'a long line is cut off with nothing to say it was')
  assert.match(head[1], /overflow:\s*hidden/)
})

test('the sign-in warning is the quota warning’s colour, and expired is the error one', () => {
  const warn = /\n\.loginwarn\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(warn, null, 'there is no rule for the sign-in warning')
  assert.match(warn[1], /var\(--warning\)/, 'the warning was drawn in a colour of its own')
  const expired = /\.loginwarn\.expired\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(expired, null)
  assert.match(expired[1], /var\(--danger\)/, 'expired is not the design’s own error colour')
})

// ------------------------------------------------ the agent's words, and mine
//
// The owner, 2026-09-25: "it might make sense for the agent responses in chat to
// not have a bubble and take more advantage of a wider width." **This is a
// deliberate departure from the design bundle**, which draws the agent side as a
// surface bubble (`design/README.md` "Message types": `agent` (left, surface
// fill); `design/desktop/README.md` "Messages": radius `20 20 20 6`). So it is
// worth a test that fails the day somebody puts the fill back by habit.
test('the agent’s words have no fill and no radius, and mine keep both', () => {
  const agent = /\.say\.agent\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(agent, null, 'there is no rule for the agent’s own words')
  assert.match(agent[1], /background:\s*none/, 'the agent is back in a bubble')
  assert.doesNotMatch(agent[1], /border-radius/, 'the agent’s words have a bubble’s radius')
  assert.match(agent[1], /width:\s*100%/, 'the agent’s words do not use the width of the column')

  // The person's own message keeps the radius and tail on the sending side,
  // with a quiet theme surface and readable ink.
  const user = /\.say\.user\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(user, null)
  assert.match(user[1], /background:\s*var\(--paper-card\)/, 'the person’s bubble lost the quiet surface')
  assert.match(user[1], /color:\s*var\(--paper-ink\)/, 'the person’s bubble lost the theme ink')
  assert.match(user[1], /border-radius:\s*20px 20px 6px 20px/, 'the person’s bubble lost the design’s radius')

  // And the 56px inset is gone from the agent's side only, which is what lets a
  // fold and a table run to the edge of the column. The person's side keeps its
  // own, so their bubble can never fill the width.
  assert.doesNotMatch(/\.line\.left\s*\{([^}]*)\}/.exec(CSS)[1], /padding/, 'the agent’s side is still inset')
  assert.match(/\.line\.right\s*\{([^}]*)\}/.exec(CSS)[1], /padding-left:\s*56px/)
})

test('the measure is on the prose and on nothing else', () => {
  // 70–75 characters is a line of prose; the 760px column is nearer 95. The
  // measure is on the blocks that are prose and is deliberately *not* on the
  // table's scroller or the code block, which want every pixel there is.
  const measure = /((?:\.say\.agent > \w+,?\s*)+)\{([^}]*)\}/.exec(CSS)
  assert.notEqual(measure, null, 'the agent’s prose has no measure')
  assert.match(measure[2], /max-width:\s*\d+ch/, 'the measure is not stated in characters')
  for (const block of ['p', 'ul', 'ol', 'blockquote', 'h1', 'h2', 'h3']) {
    assert.match(measure[1], new RegExp(`\\.say\\.agent > ${block},`), `${block} is outside the measure`)
  }
  assert.doesNotMatch(measure[1], /tablewrap|pre/, 'a table or a code block was given the measure for prose')
  // Both of those are still capped by the column itself and nothing else.
  assert.match(/\.md pre\s*\{([^}]*)\}/.exec(CSS)[1], /max-width:\s*100%/)
  assert.match(/\.md \.tablewrap\s*\{([^}]*)\}/.exec(CSS)[1], /max-width:\s*100%/)
})

// **The clamp has two homes and this is the drift check.** The height a sent
// message folds at is a CSS variable, because the fade and the `max-height` are
// CSS; the page measures a node against the same number to decide whether to
// draw a control at all. Two homes, one figure, and a test that fails if they
// ever disagree.
test('the fold height in the stylesheet is the one app.js measures against', () => {
  const declared = /--mine-max:\s*(\d+)px/.exec(CSS)
  assert.notEqual(declared, null, 'the stylesheet does not name the height a long message folds at')
  const APP = readFileSync(new URL('../public/page/transcript.js', import.meta.url), 'utf8')
  const measured = /const MINE_MAX_PX = (\d+)/.exec(APP)
  assert.notEqual(measured, null, 'app.js no longer names the height it measures against')
  assert.equal(declared[1], measured[1], 'the clamp and the measurement are two different heights')

  const clamp = /\.say\.user\.clamped\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(clamp, null, 'a long message has nothing clamping it')
  assert.match(clamp[1], /max-height:\s*var\(--mine-max\)/)
  assert.match(clamp[1], /overflow:\s*clip/)
  // The fade is the bubble's own fill over the text, not a mask on the element:
  // a mask would fade the fill itself and take the bubble's corner with it.
  const fade = /\.say\.user\.clamped::after\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(fade, null, 'a clamped message is cut off with nothing to say it was')
  assert.match(fade[1], /linear-gradient\(to top, color-mix\(in srgb, var\(--paper-card\) 98%/)
  assert.match(fade[1], /pointer-events:\s*none/)
})

// The thread header's blob, and the one rule that matters about it: `working`
// and `waiting` both breathe. The owner, 2026-10-02: "in the waiting state, the
// chat should still have the icon pulsing. The pulsing is signifying that that
// session is active and will have more output coming at some point."
// `test/thread.test.js` asserts which nodes the page draws for each; this is the
// half that says what they do.
test('the chat’s blob breathes while working and while waiting, and reduced motion stills it', () => {
  assert.match(/\n\.breathe\s*\{([^}]*)\}/.exec(CSS)[1], /animation:\s*loamBreathe/)
  assert.equal(/\.halo\b/.exec(CSS), null, 'the steady waiting halo is back')
  const reduced = /@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{([^}]*)\}/.exec(CSS)
  assert.notEqual(reduced, null)
  assert.match(reduced[1], /animation-iteration-count:\s*1/)
})

// The owner, 2026-09-29: dragging on a sheet "actually drags it down and
// refreshes the page". A phone's refresh gesture is the viewport's overscroll,
// started by a pull that reaches the document — directly, or chained up from a
// scroller already at its end. So the root refuses it, and every rule that makes
// something scroll contains its own overscroll: a scroller added later without
// it is the refresh coming back, and this is what says so.
test('no pull-to-refresh: the root refuses overscroll and every scroller contains its own', () => {
  const all = rules(CSS)
  const root = all.filter((r) => /^(html|body)$/.test(r.selector))
  assert.equal(root.length >= 2, true, 'the root has no rule of its own')
  for (const r of root) assert.match(r.body, /overscroll-behavior:\s*none/, `${r.selector} lets the page overscroll`)
  const scrollers = all.filter((r) => /overflow(-[xy])?\s*:\s*(auto|scroll)/.test(r.body))
  assert.equal(scrollers.length > 10, true)
  const loose = scrollers.filter((r) => !/overscroll-behavior(-[xy])?\s*:\s*(contain|none)/.test(r.body)).map((r) => r.selector)
  assert.deepEqual(loose, [], 'a scroller lets its overscroll reach the page')
  for (const name of ['.msgs', '.scroll', '.sheet', '.actions-sheet', '.editor-body', '.profile-body']) {
    assert.ok(scrollers.some((r) => r.selector === name), `${name} is not one of the scrollers any more`)
  }
})

test('a sheet’s handle row takes the touch, and the ✕ is the design’s 32px round ghost', () => {
  const all = rules(CSS)
  const top = all.find((r) => r.selector === '.sheet-top')
  assert.ok(top, 'there is no handle row')
  assert.match(top.body, /touch-action:\s*none/)
  const x = all.find((r) => r.selector === '.iconbtn.sheet-x')
  assert.match(x.body, /width:\s*32px/)
  assert.match(x.body, /height:\s*32px/)
  assert.match(all.find((r) => r.selector === '.iconbtn').body, /border-radius:\s*999px/)
  assert.match(all.find((r) => r.selector === '.scrim').body, /touch-action:\s*none/)
})

// The owner, 2026-09-30: a formatted block "is its own separate scrollable
// container". A code block and a table slide sideways and never up and down, so
// a vertical swipe that lands on one is the thread's; and the copy button is a
// ghost a pointer finds on hover and a phone always sees.
test('a code block and a table scroll sideways only, and the copy button shows by hover or always', () => {
  const all = rules(CSS)
  for (const selector of ['.md pre', '.md .tablewrap']) {
    const rule = all.find((r) => r.selector === selector && r.media.length === 0)
    assert.match(rule.body, /overflow-y:\s*hidden/, `${selector} is a vertical scroller inside the thread`)
    assert.match(rule.body, /overscroll-behavior-x:\s*contain/, `${selector} lets a sideways overscroll reach the page`)
    assert.doesNotMatch(rule.body, /max-height/, `${selector} is clipped into a box of its own`)
    assert.doesNotMatch(rule.body, /overscroll-behavior:\s*contain/, `${selector} keeps the vertical swipe from the thread`)
  }
  const copy = all.find((r) => r.selector === '.copyblk' && r.media.length === 0)
  assert.match(copy.body, /user-select:\s*none/, 'a long-press in the block would select the button’s word')
  const hovering = all.filter((r) => r.media.some((m) => /hover:\s*hover/.test(m)))
  assert.match(hovering.find((r) => r.selector === '.copyblk').body, /opacity:\s*0/)
  assert.ok(hovering.some((r) => r.selector.includes('.block:hover .copyblk')), 'hovering a block does not show its button')
  const touch = all.filter((r) => r.media.some((m) => /hover:\s*none/.test(m)))
  assert.match(touch.find((r) => r.selector === '.copyblk').body, /opacity:\s*1/, 'a phone cannot see the button')
})

test('the line where the reply will be is a calm dot that is still under reduced motion', () => {
  const all = rules(CSS)
  const dot = all.find((r) => r.selector === '.awaiting-dot' && r.media.length === 0)
  assert.match(dot.body, /animation:\s*loamPulse/)
  assert.doesNotMatch(dot.body, /loamSpin/, 'the dot is a spinner')
  const still = all.find((r) => r.selector === '.awaiting-dot' && r.media.some((m) => /prefers-reduced-motion/.test(m)))
  assert.match(still.body, /animation:\s*none/)
})

test('the ring’s classes are its own: no rule for some other block can land on it', () => {
  // `qring empty` met `.empty`, the page's empty-state block with 36px by 30px
  // of padding, and every chat with no quota reading drew its provider mark in
  // a ring swollen past the buttons beside it (2026-10-01). Every class the
  // ring wears is now `qring`-prefixed, and every rule naming one of them is a
  // ring rule.
  const tokens = ['qring', ...['empty', 'calm', 'warn', 'high'].map((l) => `qring-${l}`)]
  for (const token of tokens) {
    const hits = RULES.map((r) => r.selector).filter((s) => new RegExp(`\\.${token}(?![\\w-])`).test(s))
    for (const s of hits) {
      for (const part of s.split(',')) {
        if (new RegExp(`\\.${token}(?![\\w-])`).test(part)) assert.match(part, /\.qring(?![\w-])/, `${part.trim()} styles a ring class outside the ring`)
      }
    }
  }
  assert.equal(RULES.some((r) => /(^|[\s,])\.empty(?![\w-])/.test(r.selector)), true, 'the block the ring collided with is still there; this test guards against it')
})

// Allow the read-aloud menu explanation to wrap inside its fixed width.
test('the read-aloud menu’s line under its word wraps and is never cut with an ellipsis', () => {
  const sub = /\n\.speakmenu \.setrow-sub\s*\{([^}]*)\}/.exec(CSS)
  assert.notEqual(sub, null, 'the menu’s row line has no rule of its own and takes the shared one-line ellipsis')
  assert.match(sub[1], /white-space:\s*normal/)
  assert.doesNotMatch(sub[1], /white-space:\s*nowrap/)
  assert.doesNotMatch(sub[1], /text-overflow:\s*ellipsis/)
  assert.doesNotMatch(sub[1], /overflow:\s*hidden/)
  // Two classes against the shared rule's one, so it wins wherever it sits;
  // and nothing later and as specific puts the one line back.
  const at = RULES.findIndex((rule) => rule.selector === '.speakmenu .setrow-sub')
  const later = RULES.slice(at + 1).filter((rule) => /(\.popover|\.speakmenu)[^,{]*\.setrow-sub/.test(rule.selector))
  assert.deepEqual(later.map((rule) => rule.selector), [], 'a later rule reaches the menu’s row line')
  const surface = readFileSync(new URL('../public/menu-surface.js', import.meta.url), 'utf8')
  assert.match(surface, /width = 360/)

})
