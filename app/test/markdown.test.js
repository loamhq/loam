// The renderer, read back node by node.
//
// The rule this file exists to hold is that **no reply ever becomes HTML**.
// `markdown.js` builds elements and text nodes and never a string of markup,
// so the two payloads below — an `<img onerror=…>` and a `<script>` — come out
// as characters in a text node. That is asserted twice: that the characters are
// on the screen, and that no `img` or `script` element exists anywhere in the
// tree. The second is the one that would catch a future rewrite that reached
// for `innerHTML`.
//
// What is fixture: the DOM is `test/fakedom.js`, so nothing here proves how a
// browser paints a heading. What it proves is which nodes the page is handed.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom } from './fakedom.js'

installDom()
const { markdown, strip, safeHref } = await import('../public/markdown.js')

// A tree small enough to read in a failure message: `p(strong("x"))`.
function shape(node) {
  if (node.nodeType === 3) return JSON.stringify(node.nodeValue)
  const attrs = node.attributes
    .filter((a) => a.name !== 'class')
    .map((a) => `${a.name}=${a.value}`)
    .join(' ')
  const cls = node.getAttribute('class')
  const head = `${node.tag}${cls ? `.${cls}` : ''}${attrs ? `[${attrs}]` : ''}`
  return `${head}(${node.childNodes.map(shape).join(',')})`
}

const draw = (src) => markdown(src).map(shape).join(' ')
const words = (src) => markdown(src).map((n) => n.textContent ?? n.nodeValue).join('\n')

// Every element in the tree, by tag, so "there is no script element" is a thing
// a test can say.
function tagsIn(nodes, out = []) {
  for (const node of nodes) {
    if (node.nodeType !== 1) continue
    out.push(node.tag)
    tagsIn(node.childNodes, out)
  }
  return out
}

// ------------------------------------------------------------------- safety

test('an HTML tag in a reply is characters on the screen, not a tag', () => {
  const nodes = markdown('Here is one: <img src=x onerror=alert(1)>')
  assert.equal(words('Here is one: <img src=x onerror=alert(1)>'), 'Here is one: <img src=x onerror=alert(1)>')
  assert.deepEqual(tagsIn(nodes), ['p'], 'a tag in a reply became an element')
})

test('a script in a reply is characters on the screen, not a script', () => {
  const src = '<script>alert("xss")</script>'
  const nodes = markdown(src)
  assert.equal(words(src), src)
  assert.deepEqual(tagsIn(nodes), ['p'], 'a script in a reply became an element')
})

test('markup inside a code fence is code, and still not markup', () => {
  const nodes = markdown('```html\n<script>alert(1)</script>\n```')
  assert.equal(shape(nodes[0]), 'pre(code.lang-html("<script>alert(1)</script>"))')
  assert.deepEqual(tagsIn(nodes), ['pre', 'code'])
})

test('only http, https and mailto become links', () => {
  assert.equal(safeHref('https://example.com/a?b=c#d'), 'https://example.com/a?b=c#d')
  assert.equal(safeHref('HTTP://EXAMPLE.COM'), 'HTTP://EXAMPLE.COM')
  assert.equal(safeHref('mailto:someone@example.com'), 'mailto:someone@example.com')
  assert.equal(safeHref('javascript:alert(1)'), null)
  assert.equal(safeHref('data:text/html,<script>alert(1)</script>'), null)
  assert.equal(safeHref('vbscript:msgbox(1)'), null)
  // The tab is the trick: a check that only looked at the first characters
  // would let this through, and a browser would strip the tab and run it.
  assert.equal(safeHref('java\tscript:alert(1)'), null)
  assert.equal(safeHref(' \n javascript:alert(1)'), null)
  // No scheme at all is not a link either: a relative path is the agent
  // talking about a file, and this page cannot follow one.
  assert.equal(safeHref('./notes/readme.md'), null)
  assert.equal(safeHref('//example.com'), null)
})

test('a link this app will not follow is left as the markdown it was written as', () => {
  assert.equal(draw('[click me](javascript:alert(1))'), 'p("[click me](javascript:alert(1))")')
  assert.equal(draw('see [the file](./src/app.js) for it'), 'p("see [the file](./src/app.js) for it")')
})

test('a link opens in a new tab and takes nothing with it', () => {
  assert.equal(
    draw('[the docs](https://example.com/docs)'),
    'p(a[href=https://example.com/docs target=_blank rel=noopener noreferrer]("the docs"))',
  )
})

test('an autolink is a link, and an angle bracket that is not one is text', () => {
  assert.equal(draw('<https://example.com>'), 'p(a[href=https://example.com target=_blank rel=noopener noreferrer]("https://example.com"))')
  assert.equal(draw('<javascript:alert(1)>'), 'p("<javascript:alert(1)>")')
})

test('an image is the link it points at, and nothing is fetched', () => {
  const nodes = markdown('![a diagram](https://example.com/d.png)')
  assert.deepEqual(tagsIn(nodes), ['p', 'a'])
  assert.equal(words('![a diagram](https://example.com/d.png)'), 'a diagram')
})

// The rule stated as a property rather than as examples: whatever an agent
// says, the page can only end up with these elements and these attributes, and
// an `href` can only ever be one of three schemes. The text is nonsense on
// purpose and the generator is deterministic, so a failure is reproducible.
test('no reply, however it is written, can produce anything but these nodes', () => {
  const bits = [
    '*', '**', '_', '~', '~~', '`', '[', ']', '(', ')', '<', '>', '#', '-', '1.', ' ', '\n', '\\', '!', 'x', '"', "'",
    'javascript:alert(1)', 'JaVaScRiPt:x', 'data:text/html,x', 'java\tscript:alert(1)',
    'https://ok', '//evil.example', '<img src=x onerror=alert(1)>', '<script>', '</script>', '```', 'onerror=',
    // The table's own characters, so the block added on 2026-09-22 is inside
    // this rule and not beside it.
    '|', '---', ':--', '--:', '| a |',
  ]
  const ALLOWED = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'code', 'ul', 'ol', 'li', 'blockquote', 'hr', 'a', 'strong', 'em', 'del', 'br', 'div', 'table', 'thead', 'tbody', 'tr', 'th', 'td'])
  const ATTRS = new Set(['class', 'href', 'target', 'rel'])

  let seed = 20260918
  const next = (n) => {
    seed ^= (seed << 13) & 0xffffffff
    seed >>>= 0
    seed ^= seed >>> 17
    seed ^= (seed << 5) & 0xffffffff
    seed >>>= 0
    return seed % n
  }

  const tags = new Set()
  const attrs = new Set()
  const hrefs = new Set()
  const walk = (node) => {
    if (node.nodeType !== 1) return
    tags.add(node.tag)
    for (const attr of node.attributes) {
      attrs.add(attr.name)
      if (attr.name === 'href') hrefs.add(attr.value)
    }
    for (const child of node.childNodes) walk(child)
  }

  for (let i = 0; i < 4000; i += 1) {
    let src = ''
    for (let j = 0, len = 1 + next(25); j < len; j += 1) src += bits[next(bits.length)]
    for (const node of markdown(src)) walk(node)
  }

  assert.deepEqual([...tags].filter((t) => !ALLOWED.has(t)), [], 'an element the page never asked for')
  assert.deepEqual([...attrs].filter((a) => !ATTRS.has(a)), [], 'an attribute the page never asked for')
  assert.deepEqual([...hrefs].filter((h) => !/^(https?|mailto):/i.test(h)), [], 'a link this app said it would not follow')
  // The generator is only worth anything if it actually reached the interesting
  // shapes, so it says what it drew.
  assert.equal(tags.has('a'), true)
  assert.equal(tags.has('pre'), true)
  assert.equal(tags.has('strong'), true)
  assert.equal(tags.has('table'), true)
})

// -------------------------------------------------------------------- blocks

test('paragraphs are paragraphs and a line break inside one is a break', () => {
  assert.equal(draw('one\ntwo\n\nthree'), 'p("one",br(),"two") p("three")')
})

test('a document (soft) joins hard-wrapped lines with a space', () => {
  const soft = (src) => markdown(src, { soft: true }).map(shape).join(' ')
  assert.equal(soft('in light and in dark, with\nFraunces for its headings.'), 'p("in light and in dark, with"," ","Fraunces for its headings.")')
  // A chat reply keeps its newline as a break.
  assert.equal(draw('one\ntwo'), 'p("one",br(),"two")')
})

test('headings come out at their level', () => {
  assert.equal(draw('# One\n\n## Two\n\n###### Six'), 'h1("One") h2("Two") h6("Six")')
  // A hash with no space is a hash.
  assert.equal(draw('#tag'), 'p("#tag")')
})

test('bold, italic, both, and struck through', () => {
  assert.equal(draw('**Shaun, Monday.**'), 'p(strong("Shaun, Monday."))')
  assert.equal(draw('*soft*'), 'p(em("soft"))')
  assert.equal(draw('_soft_'), 'p(em("soft"))')
  assert.equal(draw('***both***'), 'p(strong(em("both")))')
  assert.equal(draw('~~gone~~'), 'p(del("gone"))')
  assert.equal(draw('a **b** c'), 'p("a ",strong("b")," c")')
})

test('a star that is not a delimiter is a star', () => {
  assert.equal(draw('2 * 3 * 4'), 'p("2 * 3 * 4")')
  assert.equal(draw('an_identifier_name'), 'p("an_identifier_name")')
  assert.equal(draw('\\*not bold\\*'), 'p("*not bold*")')
})

test('inline code is code, and the marks inside it are not marks', () => {
  assert.equal(draw('run `npm test` now'), 'p("run ",code("npm test")," now")')
  assert.equal(draw('`**not bold**`'), 'p(code("**not bold**"))')
  // A double-backtick span can hold a backtick.
  assert.equal(draw('``a ` b``'), 'p(code("a ` b"))')
  // An unclosed backtick is a backtick.
  assert.equal(draw('a ` b'), 'p("a ` b")')
})

test('a fenced block keeps its lines, and its language is a class', () => {
  assert.equal(draw('```js\nconst a = 1\nconst b = 2\n```'), 'pre(code.lang-js("const a = 1\\nconst b = 2"))')
  assert.equal(draw('```\nplain\n```'), 'pre(code("plain"))')
  // The fence's info string is an agent's word and is never trusted further.
  assert.equal(draw('```js onerror="x"\nq\n```'), 'pre(code.lang-js("q"))')
})

test('a fence that has not closed yet is already a code block', () => {
  // This is what a message mid-stream looks like.
  assert.equal(draw('```py\nimport os'), 'pre(code.lang-py("import os"))')
})

test('lists, ordered and not, and one inside another', () => {
  assert.equal(draw('- one\n- two'), 'ul(li("one"),li("two"))')
  assert.equal(draw('1. one\n2. two'), 'ol(li("one"),li("two"))')
  assert.equal(draw('3. three\n4. four'), 'ol[start=3](li("three"),li("four"))')
  assert.equal(draw('- one\n  - deeper\n- two'), 'ul(li("one",ul(li("deeper"))),li("two"))')
  assert.equal(draw('- a **bold** item'), 'ul(li("a ",strong("bold")," item"))')
})

test('a list with air in it keeps the air, and stays one list', () => {
  assert.equal(draw('- one\n\n- two'), 'ul(li(p("one")),li(p("two")))')
})

test('a list can start straight after a paragraph, the way an agent writes one', () => {
  assert.equal(draw('I did three things:\n- one\n- two'), 'p("I did three things:") ul(li("one"),li("two"))')
})

test('a quote is a quote, and a line in it that forgot its mark is still in it', () => {
  assert.equal(draw('> it said this\n> and this'), 'blockquote(p("it said this",br(),"and this"))')
  assert.equal(draw('> it said this\nand this'), 'blockquote(p("it said this",br(),"and this"))')
})

test('a rule is a rule and three dashes are not a list', () => {
  assert.equal(draw('a\n\n---\n\nb'), 'p("a") hr() p("b")')
})

// ------------------------------------------------------------------ tables
//
// The owner, 2026-09-22: "render tables in the chat beautifully". They were the
// one thing agents write often that this renderer left as the pipes it was
// written with. What is fixture here is the same as everywhere else in this
// file: these are the nodes the page is handed, and the padding, the rule
// between the rows and the sideways scroll are read out of the stylesheet in
// `test/style.test.js` instead.

test('a three-column table is a table, header and body apart', () => {
  const src = ['| Agent | Chats | Spend |', '| --- | --- | --- |', '| Scout | 12 | $3.40 |', '| Sable | 3 | $0.18 |'].join('\n')
  assert.equal(
    draw(src),
    'div.tablewrap(' +
      'table(' +
      'thead(tr(th("Agent"),th.ta-right("Chats"),th.ta-right("Spend")))' +
      ',tbody(' +
      'tr(td("Scout"),td.ta-right("12"),td.ta-right("$3.40"))' +
      ',tr(td("Sable"),td.ta-right("3"),td.ta-right("$0.18"))' +
      ')))',
  )
})

test('the dashes say how a column lines up, and a column of numbers says so itself', () => {
  // Asked for: left, centre, right. The fourth column asks for nothing and is
  // all numbers, so it is lined up on the right without being told.
  const src = ['| a | b | c | d |', '| :-- | :-: | --: | --- |', '| one | two | three | 4 |'].join('\n')
  const head = markdown(src)[0].childNodes[0].childNodes[0].childNodes[0]
  assert.deepEqual(
    head.childNodes.map((cell) => cell.getAttribute('class')),
    ['ta-left', 'ta-center', 'ta-right', 'ta-right'],
  )
  // And one cell of prose in a column is a column of prose: nothing in it is
  // moved to the right, because the guess is only ever made when it is safe.
  const mixed = ['| a | b |', '| --- | --- |', '| one | 4 |', '| two | later |'].join('\n')
  const row = markdown(mixed)[0].childNodes[0].childNodes[1].childNodes[0]
  assert.deepEqual(row.childNodes.map((cell) => cell.getAttribute('class')), [null, null])
})

test('a cell is markdown, and a pipe a writer meant is a pipe', () => {
  const src = ['| what | how |', '| --- | --- |', '| **bold** and `code` | a \\| b |'].join('\n')
  assert.equal(
    draw(src),
    'div.tablewrap(table(' +
      'thead(tr(th("what"),th("how")))' +
      ',tbody(tr(td(strong("bold")," and ",code("code")),td("a | b"))))' +
      ')',
  )
})

test('a long cell is one cell, and a short row is padded to the header', () => {
  const long = 'a cell with a great deal of prose in it, which is what makes a table wider than a phone'
  const src = ['| one | two | three |', '| --- | --- | --- |', `| ${long} | b |`].join('\n')
  const body = markdown(src)[0].childNodes[0].childNodes[1]
  assert.equal(body.childNodes.length, 1, 'the row was split')
  assert.deepEqual(body.childNodes[0].childNodes.map((c) => c.textContent), [long, 'b', ''])
})

test('a table can follow a sentence with no blank line, and ends where it ends', () => {
  const src = ['Here is what ran:', '| a | b |', '| --- | --- |', '| 1 | 2 |', 'and that was all.'].join('\n')
  assert.equal(
    draw(src),
    'p("Here is what ran:") ' +
      'div.tablewrap(table(thead(tr(th.ta-right("a"),th.ta-right("b"))),tbody(tr(td.ta-right("1"),td.ta-right("2"))))) ' +
      'p("and that was all.")',
  )
})

test('pipes that are not a table are still the characters they were', () => {
  // No delimiter row, so it is a paragraph — which is what it was before
  // tables existed and what it has to stay.
  assert.equal(draw('| a | b |\n| c | d |'), 'p("| a | b |",br(),"| c | d |")')
  // And a table's own markup can still never become an element of the page's
  // choosing: the cells are text nodes like everything else here.
  const nodes = markdown('| a |\n| --- |\n| <script>x</script> |')
  assert.deepEqual(tagsIn(nodes), ['div', 'table', 'thead', 'tr', 'th', 'tbody', 'tr', 'td'])
})

test('the status box gets a table as its words, not as its pipes', () => {
  assert.equal(strip('| a | b |\n| --- | --- |\n| 1 | 2 |'), 'a b 1 2')
})

test('four spaces after a blank line is code; a wrapped sentence is not', () => {
  assert.equal(draw('    indented()'), 'pre(code("indented()"))')
  assert.equal(draw('a sentence\n    that wrapped'), 'p("a sentence",br(),"that wrapped")')
})

test('nothing in, nothing out', () => {
  assert.deepEqual(markdown(''), [])
  assert.deepEqual(markdown('   \n\n  '), [])
  assert.deepEqual(markdown(null), [])
})

// ------------------------------------------------------------------- stripped

test('the status box gets the words without the marks', () => {
  assert.equal(strip('**Shaun, Monday.** The build is green.'), 'Shaun, Monday. The build is green.')
  assert.equal(strip('# Done\n\n- one\n- two'), 'Done one two')
  assert.equal(strip('see `src/app.js`'), 'see src/app.js')
  assert.equal(strip('[the docs](https://example.com)'), 'the docs')
  assert.equal(strip('a line\nand another'), 'a line and another')
  assert.equal(strip(null), null)
  assert.equal(strip(''), '')
})

// ------------------------------------------------------- the copy buttons
//
// A code block and a table are handed to the page's `copy` with their source —
// the code as written, the table as the rows it was written in — and the node it
// answers goes in the block's corner. Without `copy` nothing is wrapped, which is
// the person's own bubble.
test('a code block and a table carry their source to the copy button, and nothing else does', () => {
  const asked = []
  const copy = (source, what) => {
    asked.push([what, source])
    return document.createElement('button')
  }
  const src = ['Here:', '', '```js', 'const a = 1', '  const b = 2', '```', '', '  | a | b |', '  |---|---|', '  | 1 | **2** |', '', '- a list', '', '  ```', '  inside', '  ```'].join('\n')
  const nodes = markdown(src, { copy })
  assert.deepEqual(asked, [
    ['code', 'const a = 1\n  const b = 2'],
    ['table', '| a | b |\n|---|---|\n| 1 | **2** |'],
    ['code', 'inside'],
  ])
  const frames = nodes.filter((n) => n.nodeName === 'DIV')
  assert.deepEqual(frames.map((n) => n.getAttribute('class')), ['block code-frame', 'block table-frame'])
  assert.deepEqual(frames.map((n) => n.childNodes.map((c) => c.nodeName)), [['PRE', 'BUTTON'], ['DIV', 'BUTTON']])

  const plain = markdown(src)
  assert.deepEqual(plain.map((n) => n.nodeName), ['P', 'PRE', 'DIV', 'UL'])
  assert.equal(plain[2].getAttribute('class'), 'tablewrap')
})
