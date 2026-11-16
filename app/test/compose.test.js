// The composer's field, read back node by node and character by character.
//
// The owner, 2026-09-22: "please make sure bullet points and numbered lists
// render as MD in the text input" and, asked, "I meant both in the input box
// and my messages after sent." The bubble half is `test/markdown.test.js`; this
// is row one.
//
// Two rules hold the file together and every test is one of them:
//
//   **The markdown is the truth.** What the field holds is a string, the
//   transforms are pure functions of that string and a caret offset into it,
//   and the drawing is built from the string. So "type a bullet list and a bold
//   word, serialise it, render it with `markdown.js`" is a round trip with an
//   answer, and it is the test at the bottom of this file.
//
//   **Nothing is ever parsed as HTML** — not the tree the browser made, not the
//   clipboard. A paste is `text/plain` and nothing else, so an `<img onerror=…>`
//   on the clipboard is characters in the field, characters in what is sent and
//   characters in the bubble.
//
// What is fixture: the DOM is `test/fakedom.js` and there is no selection in
// it, so where a caret *lands* after the field is redrawn is not proved here.
// The arithmetic under it is — `visibleCaret` is a pure function and is tested
// — and the placing of it is a `Range`, which wants a browser. The selection
// below is a stub: enough to prove that a key reaches the transform and that
// the field is redrawn, not that Android Chrome agrees.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom } from './fakedom.js'

installDom()
const {
  richField,
  readField,
  setMarkdown,
  normalise,
  renderedText,
  visibleCaret,
  enterAt,
  backspaceAt,
  insertAt,
  nodesFor,
} = await import('../public/compose.js')
const { markdown } = await import('../public/markdown.js')

// A tree small enough to read in a failure message.
function shape(node) {
  if (node.nodeType === 3) return JSON.stringify(node.nodeValue)
  const cls = node.getAttribute?.('class')
  return `${node.tag}${cls ? `.${cls}` : ''}(${node.childNodes.map(shape).join(',')})`
}

const field = (md = '') => {
  const el = document.createElement('div')
  setMarkdown(el, md)
  return el
}
const drawn = (el) => el.childNodes.map(shape).join(' ')

// What a browser leaves behind when somebody types into an empty field: a bare
// text node, no block of its own. Every "as it is typed" test below goes
// through this, because that is the tree `normalise()` actually meets.
const typed = (el, text) => {
  while (el.firstChild) el.removeChild(el.firstChild)
  el.appendChild(document.createTextNode(text))
  normalise(el)
  return el
}

// ------------------------------------------------------------------- the marks

test('a line that starts with a dash or a star becomes a bullet as it is typed', () => {
  const el = field()
  typed(el, '- milk')
  assert.equal(drawn(el), 'ul(li("milk"))', 'a typed `- ` is still two characters on the screen')
  assert.equal(readField(el).md, '- milk')

  // A star is the other mark markdown gives, and it draws the same bullet — but
  // **one mark goes on the wire**: a `<ul>` does not remember which character
  // made it, and inventing a memory for it would be this field keeping state
  // the drawing cannot state. So `* milk` is typed and `- milk` is sent, which
  // is the same list to every reader of markdown there is.
  const star = field()
  typed(star, '* milk')
  assert.equal(drawn(star), 'ul(li("milk"))')
  assert.equal(readField(star).md, '- milk')

  // And the mark alone is a bullet with nothing in it, which is what a person
  // sees the instant they type the space.
  const empty = field()
  typed(empty, '- ')
  assert.equal(drawn(empty), 'ul(li(br()))')
})

test('`1. ` is a numbered item, and the numbers are the list’s own', () => {
  const el = field('1. first\n2. second\n3. third')
  assert.equal(drawn(el), 'ol(li("first"),li("second"),li("third"))')
  // The `<ol>` draws its own numbers, so they are not text in the field — and
  // they are read back out as the positions they are, whatever was typed.
  assert.equal(renderedText('1. first\n2. second'), 'first\nsecond')
  assert.equal(readField(field('5. first\n5. second')).md, '1. first\n2. second')
})

test('a bullet run and a numbered run are two lists, not one', () => {
  assert.equal(drawn(field('- a\n1. b')), 'ul(li("a")) ol(li("b"))')
  assert.equal(drawn(field('- a\nplain\n- b')), 'ul(li("a")) div.ln("plain") ul(li("b"))')
})

test('bold and code render once they close, and not before', () => {
  assert.equal(drawn(field('**bold**')), 'div.ln(strong("bold"))')
  assert.equal(drawn(field('`code`')), 'div.ln(code("code"))')
  // Still being typed: the characters are the characters.
  assert.equal(drawn(field('**bol')), 'div.ln("**bol")')
  assert.equal(drawn(field('`cod')), 'div.ln("`cod")')
  // Empty marks are not marks. `****` is four characters, not an empty bold.
  assert.equal(drawn(field('****')), 'div.ln("****")')
  assert.equal(drawn(field('a **b** c `d` e')), 'div.ln("a ",strong("b")," c ",code("d")," e")')
  // And a mark inside a list item is a mark: the two are independent.
  assert.equal(drawn(field('- a **b**')), 'ul(li("a ",strong("b")))')
})

test('a mark that closes as it is typed redraws the field, and nothing else does', () => {
  const el = field()
  // Ordinary characters: the tree the browser made is left exactly as it is,
  // which is the caret, the undo stack and any composition in flight left alone.
  typed(el, 'hello')
  assert.equal(drawn(el), '"hello"', 'ordinary typing rebuilt the field under the caret')
  typed(el, '**bold')
  assert.equal(drawn(el), '"**bold"')
  typed(el, '**bold**')
  assert.equal(drawn(el), 'div.ln(strong("bold"))')
})

test('an empty field is empty, so the placeholder is drawn', () => {
  // The placeholder is the stylesheet's `:empty::before`, and a field holding
  // one empty block is not `:empty` to CSS — so emptying it by hand empties it.
  const el = field('- a')
  setMarkdown(el, '')
  assert.equal(el.childNodes.length, 0)
  const left = field()
  left.appendChild(document.createElement('div'))
  left.firstChild.appendChild(document.createElement('br'))
  normalise(left)
  assert.equal(left.childNodes.length, 0, 'a block the browser left behind kept the placeholder away')
})

// -------------------------------------------------------------- Enter and back

test('Enter inside a list continues it, and Enter on an empty item ends it', () => {
  // The tail of the line goes into the new item: Enter in the middle of an item
  // splits it, as it does in any editor.
  assert.deepEqual(enterAt('- milk', 6), { md: '- milk\n- ', caret: 9 })
  assert.deepEqual(enterAt('- milk and eggs', 6), { md: '- milk\n-  and eggs', caret: 9 })
  assert.deepEqual(enterAt('* milk', 6), { md: '* milk\n* ', caret: 9 }, 'the list came back with the other mark')
  assert.deepEqual(enterAt('1. first', 8), { md: '1. first\n2. ', caret: 12 })
  assert.deepEqual(enterAt('1. a\n2. b', 9), { md: '1. a\n2. b\n3. ', caret: 13 })

  // An item with nothing in it is the way out: the item becomes a line of plain
  // text, and the list above it is left alone.
  assert.deepEqual(enterAt('- milk\n- ', 9), { md: '- milk\n', caret: 7 })
  assert.deepEqual(enterAt('1. a\n2. ', 8), { md: '1. a\n', caret: 5 })

  // Outside a list, Enter is a new line, which is what it has always been.
  assert.deepEqual(enterAt('a thought', 9), { md: 'a thought\n', caret: 10 })
  assert.deepEqual(enterAt('ab', 1), { md: 'a\nb', caret: 2 })
})

test('Backspace at the start of an item turns it back into text, and nowhere else', () => {
  assert.deepEqual(backspaceAt('- milk', 2), { md: 'milk', caret: 0 })
  assert.deepEqual(backspaceAt('a\n1. milk', 5), { md: 'a\nmilk', caret: 2 })
  // One character in is an ordinary backspace, and so is the end of the item,
  // and so is a line that is not one: the browser's own key stands.
  assert.equal(backspaceAt('- milk', 3), null)
  assert.equal(backspaceAt('- milk', 6), null)
  assert.equal(backspaceAt('milk', 0), null)
})

// ------------------------------------------------------------------ the caret

test('a caret in the markdown is a caret on the screen', () => {
  // The marks are not on the screen, so a caret past them is fewer characters
  // in than it is in the text: `- ` is two characters and no width.
  assert.equal(visibleCaret('- milk', 6), 4)
  assert.equal(visibleCaret('- milk', 2), 0)
  assert.equal(visibleCaret('**bold**', 8), 4)
  // A mark still being typed is its own characters, so the caret is where the
  // characters are — which is what keeps the caret still while `**bo` is typed.
  assert.equal(visibleCaret('**bo', 4), 4)
  assert.equal(visibleCaret('- a\n- b', 7), 3, 'the line break between two items was lost')
})

test('the field is read back with the caret in it', () => {
  const el = field('- milk\n- eggs')
  const eggs = el.firstChild.childNodes[1].firstChild
  const read = readField(el, { node: eggs, offset: 2 })
  assert.equal(read.md, '- milk\n- eggs')
  assert.equal(read.text, 'milk\neggs', 'the marks were counted as things on the screen')
  assert.equal(read.caret, 11, 'the caret was read at the wrong place in the markdown')
  assert.equal(read.md.slice(0, read.caret), '- milk\n- eg')
  // No caret asked for is no caret answered, which is every call the value
  // accessor makes.
  assert.equal(readField(el).caret, null)
})

// A selection over the fake DOM: enough to carry a caret into the field's own
// key handling and to be written back. What it proves is that the key reaches
// the transform; where a browser then paints the caret is not a thing this can
// say.
function selectionAt(node, offset) {
  const ranges = []
  globalThis.getSelection = () => ({
    focusNode: node,
    focusOffset: offset,
    removeAllRanges: () => ranges.splice(0, ranges.length),
    addRange: (range) => ranges.push(range),
  })
  document.createRange = () => ({
    setStart(n, o) {
      this.node = n
      this.offset = o
    },
    selectNodeContents(n) {
      this.node = n
    },
    collapse() {},
  })
  return ranges
}

test('the key goes to the field, and the field redraws itself', () => {
  const changes = []
  const el = richField(document.createElement('div'), { changed: () => changes.push(el.value) })
  el.value = '- milk'
  const text = el.firstChild.firstChild.firstChild
  selectionAt(text, 4)

  let taken = 0
  el.fire('keydown', { key: 'Enter', preventDefault: () => (taken += 1) })
  assert.equal(taken, 1, 'the field left Enter to the browser inside a list')
  assert.equal(el.value, '- milk\n- ')
  assert.equal(drawn(el), 'ul(li("milk"),li(br()))')
  assert.equal(changes.at(-1), '- milk\n- ', 'the draft was not told the field had changed')

  // Backspace at the start of the item it is now in: the item goes back to
  // being a line of text, and the list above it keeps its one item.
  selectionAt(el.firstChild.childNodes[1], 0)
  taken = 0
  el.fire('keydown', { key: 'Backspace', preventDefault: () => (taken += 1) })
  assert.equal(taken, 1)
  assert.equal(el.value, '- milk\n')
  assert.equal(drawn(el), 'ul(li("milk")) div.ln(br())')

  // A browser with no caret to give — which is every other test in this suite —
  // gets the browser's own key back rather than a transform on a guess.
  globalThis.getSelection = () => null
  taken = 0
  el.fire('keydown', { key: 'Enter', preventDefault: () => (taken += 1) })
  assert.equal(taken, 0, 'the field took a key it could not place a caret for')
})

// ------------------------------------------------------------------ the paste

test('a paste is characters, and the clipboard’s HTML is never asked for', () => {
  const el = richField(document.createElement('div'), {})
  el.value = 'before '
  selectionAt(el.firstChild.firstChild, 7)

  const asked = []
  let taken = 0
  el.fire('paste', {
    preventDefault: () => (taken += 1),
    clipboardData: {
      getData: (type) => {
        asked.push(type)
        return type === 'text/plain' ? '- milk\n- eggs' : '<ul><li>milk</li></ul>'
      },
    },
  })
  assert.equal(taken, 1, 'the browser was left to paste, which is how HTML gets in')
  assert.deepEqual(asked, ['text/plain'], 'the clipboard was asked for something other than text')
  assert.equal(el.value, 'before - milk\n- eggs')

  // The hostile payload: on the clipboard as text, it is text in the field, text
  // in what is sent and — `test/markdown.test.js` and `test/list.test.js` — text
  // in the bubble. No element of its own exists anywhere in the field.
  const hostile = richField(document.createElement('div'), {})
  hostile.fire('paste', {
    preventDefault: () => {},
    clipboardData: { getData: () => '<img src=x onerror=alert(1)><script>alert(2)</script>' },
  })
  assert.equal(hostile.value, '<img src=x onerror=alert(1)><script>alert(2)</script>')
  assert.equal(drawn(hostile), 'div.ln("<img src=x onerror=alert(1)><script>alert(2)</script>")')
  assert.deepEqual(tagsIn(hostile), ['div'], 'the clipboard built an element')
})

test('an image on the clipboard is handed out as a file, and never drawn into the field', () => {
  const taken = []
  const el = richField(document.createElement('div'), { files: (entries) => taken.push(...entries) })
  const shot = { name: '', size: 900, type: 'image/png' }
  el.fire('paste', {
    preventDefault: () => {},
    clipboardData: {
      getData: () => '',
      items: [{ kind: 'file', type: 'image/png', getAsFile: () => shot }],
    },
  })
  // Out, not in: an image in a `contenteditable` is a node with a `src` on it,
  // which is the one thing this field never builds — what a person pasted is a
  // file, and a file is the composer's business and not the field's.
  assert.equal(taken.length, 1)
  assert.equal(taken[0].file, shot)
  assert.match(taken[0].name, /^pasted-\d{8}T\d{6}Z\.png$/)
  assert.equal(el.value, '', 'the clipboard put something in the field')

  // A clipboard carrying both is both: the words land in the field and the
  // screenshot goes up beside them.
  el.fire('paste', {
    preventDefault: () => {},
    clipboardData: {
      getData: (type) => (type === 'text/plain' ? 'like this:' : ''),
      items: [{ kind: 'file', type: 'image/png', getAsFile: () => shot }],
    },
  })
  assert.equal(taken.length, 2)
  assert.equal(el.value, 'like this:')
})

// **The other way an Android keyboard pastes a picture**, which is the whole of
// why the owner could not paste one on their phone: some keyboards fire no `paste`
// event at all and deliver the file as a `beforeinput` whose `inputType` is
// `insertFromPaste`, with a `dataTransfer` on it rather than a clipboard.
test('a beforeinput carrying a file is a paste, and one carrying none is left alone', () => {
  const taken = []
  const el = richField(document.createElement('div'), { files: (entries) => taken.push(...entries) })
  const shot = { name: 'screenshot.png', size: 900, type: 'image/png' }
  let prevented = 0

  el.fire('beforeinput', {
    inputType: 'insertFromPaste',
    preventDefault: () => (prevented += 1),
    dataTransfer: { files: [shot] },
  })
  assert.equal(taken.length, 1)
  assert.equal(taken[0].file, shot)
  // A clipboard that named the file keeps the name; only the one with no name
  // of its own is given one.
  assert.equal(taken[0].name, 'screenshot.png')
  assert.equal(prevented, 1, 'the browser was left to draw an <img> into the field')
  assert.equal(el.value, '')

  // The ordinary paste of text, on a browser that sends both events: the
  // `paste` handler has the characters and preventing this one would take them
  // away, so nothing here touches it.
  el.fire('beforeinput', { inputType: 'insertFromPaste', preventDefault: () => (prevented += 1), dataTransfer: { files: [], items: [] } })
  el.fire('beforeinput', { inputType: 'insertText', preventDefault: () => (prevented += 1), data: 'a' })
  el.fire('beforeinput', { inputType: 'insertFromPaste', preventDefault: () => (prevented += 1) })
  assert.equal(prevented, 1)
  assert.equal(taken.length, 1)
})

function tagsIn(node, out = []) {
  for (const child of node.childNodes ?? []) {
    if (child.nodeType !== 1) continue
    out.push(child.tag)
    tagsIn(child, out)
  }
  return out
}

// ------------------------------------------------------------- the round trip

test('what the field shows is what the bubble draws: the same markdown, twice', () => {
  // The brief's round trip. A bullet list and a bold word are typed, the field
  // is serialised, and the serialised text is rendered by `markdown.js` — the
  // renderer the person's own bubble uses. The two trees are the same shape and
  // the same words, which is the whole of "it sends markdown".
  const el = field()
  typed(el, '- milk\n- **eggs**, a dozen\n\nand `bread`')
  const md = readField(el).md
  assert.equal(md, '- milk\n- **eggs**, a dozen\n\nand `bread`')

  assert.equal(drawn(el), 'ul(li("milk"),li(strong("eggs"),", a dozen")) div.ln(br()) div.ln("and ",code("bread"))')

  const bubble = markdown(md).map(shape).join(' ')
  assert.equal(bubble, 'ul(li("milk"),li(strong("eggs"),", a dozen")) p("and ",code("bread"))')

  // Same words, in the same order, with no mark left on the screen on either
  // side. The field draws a blank line as a line and the bubble draws it as a
  // paragraph boundary, which is the one difference and is not a word.
  const lines = (text) =>
    text
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line !== '')
  assert.deepEqual(lines(renderedText(md)), ['milk', 'eggs, a dozen', 'and bread'])
  assert.deepEqual(
    lines(markdown(md).map((n) => n.textContent ?? n.nodeValue ?? '').join('\n')),
    ['milkeggs, a dozen', 'and bread'],
    'the bubble draws the two items as one block’s text, which is the list element doing its job',
  )
})

test('a numbered list round trips, and so does a line that only looks like one', () => {
  const el = field()
  typed(el, '1. first\n2. second')
  assert.equal(readField(el).md, '1. first\n2. second')
  assert.equal(markdown(readField(el).md).map(shape).join(' '), 'ol(li("first"),li("second"))')

  // `-5 degrees` is not a list and never was: the mark wants a space after it.
  assert.equal(drawn(field('-5 degrees')), 'div.ln("-5 degrees")')
  assert.equal(drawn(field('2026. what a year')), 'ol(li("what a year"))')
})

test('the field holds what it is given back, whatever the app puts in it', () => {
  // The accessors are the seam the rest of the app talks through: a draft
  // restored from storage, a dictated sentence, a message cleared on send.
  const el = richField(document.createElement('div'), {})
  el.value = 'first line\nsecond line'
  assert.equal(el.value, 'first line\nsecond line', 'a plain draft did not survive the field')
  el.value = '- a\n- b'
  assert.equal(el.value, '- a\n- b')
  el.value = ''
  assert.equal(el.value, '')
  assert.equal(el.childNodes.length, 0)

  // And the placeholder is an attribute, written only when it changes.
  el.placeholder = 'Message Scout'
  assert.equal(el.getAttribute('data-placeholder'), 'Message Scout')
  assert.equal(el.placeholder, 'Message Scout')
})

test('the shape of the markdown is the shape of the nodes', () => {
  // `nodesFor` is the one place a string becomes elements, and every element it
  // makes is one of five: ul, ol, li, div, strong, code, br.
  const made = nodesFor('- a\n1. b\n\n**c** `d`')
  const tags = []
  const walk = (nodes) => {
    for (const node of nodes) {
      tags.push(node.tag)
      walk(node.childNodes.filter((c) => c.nodeType === 1))
    }
  }
  walk(made)
  assert.deepEqual([...new Set(tags)].sort(), ['br', 'code', 'div', 'li', 'ol', 'strong', 'ul'])
  assert.deepEqual(insertAt('ab', 1, 'X'), { md: 'aXb', caret: 2 })
  assert.deepEqual(insertAt('ab', 2, '\r\nc'), { md: 'ab\nc', caret: 4 }, 'a pasted line ending stayed a pair')
})

test('mentions are subtle spans and preserve markdown, caret offsets and literal HTML', () => {
  const md = 'Hello @quill and @sprout\n- Bring **one** idea to @scout\ncontact@example.com <script>'
  const el = field(md)
  assert.equal(readField(el).md, md)
  assert.match(drawn(el), /span\.mention\("@quill"\)/)
  assert.doesNotMatch(drawn(el), /span\.mention\("@example"\)/)
  assert.equal(visibleCaret('Hello @quill', 12), 12)
  assert.match(readField(el).md, /<script>/)
})
