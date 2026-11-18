// An agent's images drawn where it wrote them (2026-11-18, evening):
// `replyImages` in chat-ui.js and the `images` option of markdown.js.
//
// What is fixture: the DOM is test/fakedom.js. What it proves is which nodes
// the page is handed and in which order; the look is the browser capture's.
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { all } from './browser.js'

installDom()
const { markdown, strip, imageRefs } = await import('../public/markdown.js')
const { replyImages } = await import('../public/chat-ui.js')

const fox = { path: 'Images/fox.png', root: 'vault', name: 'fox.png', type: 'image/png', size: 9, width: 1600, height: 900, alt: 'a fox', refs: ['Images/fox.png'] }
const owl = { path: 'iris/owl.jpg', root: 'spaces', name: 'owl.jpg', type: 'image/jpeg', size: 9, width: 600, height: 800, refs: ['/home/x/ai-labs/iris/owl.jpg'] }
const shape = (nodes) => nodes.map((n) => n.nodeType === 3 ? 'text' : `${n.tag}.${n.getAttribute('class') ?? ''}`).join(' ')
const render = (text, message, opts) => {
  const pictures = replyImages(message, 't:m1', opts)
  return { pictures, nodes: markdown(text, { images: pictures.images }) }
}
const real = (node) => node.__keep ?? node
const deep = (nodes) => { const host = { childNodes: [] }; const out = []; const walk = (n) => { n = real(n); out.push(n); for (const c of n.childNodes ?? []) walk(c) }; nodes.forEach(walk); void host; return out }
const byClass = (nodes, cls) => deep(nodes).filter((n) => n.nodeType === 1 && (n.getAttribute('class') ?? '').split(/\s+/).includes(cls))

test('an image mid-text is drawn at its place, between the words around it', () => {
  const { nodes } = render('Before.\n\n![a fox](Images/fox.png)\n\nAfter.', { attachments: [fox] })
  assert.equal(shape(nodes), 'p. div.reply-figures p.')
  assert.equal(nodes[0].textContent, 'Before.')
  assert.equal(nodes[2].textContent, 'After.')
  const img = deep(nodes).find((n) => n.tag === 'img')
  const url = new URL(img.getAttribute('src'), 'http://fixture')
  assert.equal(url.pathname, '/api/files/download')
  assert.equal(url.searchParams.get('root'), 'vault')
  assert.equal(url.searchParams.get('path'), 'Images/fox.png')
  assert.equal(img.getAttribute('width'), '1600')
  assert.equal(img.getAttribute('height'), '900')
  assert.equal(byClass(nodes, 'reply-caption')[0].textContent, 'a fox')
  assert.ok(!deep(nodes).some((n) => n.nodeType === 3 && /!\[|fox\.png/.test(n.nodeValue)), 'no raw syntax, no filename')
})

test('an image inside a sentence splits it: the words, the picture, the rest', () => {
  const { nodes } = render('Look at this ![a fox](Images/fox.png) and tell me.', { attachments: [fox] })
  assert.equal(shape(nodes), 'p. div.reply-figures p.')
  assert.equal(nodes[0].textContent, 'Look at this')
  assert.equal(nodes[2].textContent, 'and tell me.')
})

test('images on consecutive lines, or a blank line apart, are one row', () => {
  for (const text of ['![a fox](Images/fox.png)\n![](/home/x/ai-labs/iris/owl.jpg)', '![a fox](Images/fox.png)\n\n![](/home/x/ai-labs/iris/owl.jpg)']) {
    const { nodes } = render(`Two:\n\n${text}\n\nDone.`, { attachments: [fox, owl] })
    assert.equal(shape(nodes), 'p. div.reply-figures p.', text)
    assert.equal(nodes[1].getAttribute('data-count'), '2')
    assert.equal(byClass(nodes, 'reply-figure').length, 2)
    assert.equal(byClass(nodes, 'reply-caption').length, 1, 'no alt, no caption')
    const row = byClass(nodes, 'reply-figures-row')[0]
    assert.match(row.getAttribute('style') ?? row.style?.cssText ?? '', /2\.5278|--ratio-sum/)
  }
})

test('a reference that is not one of the message’s files stays as it was written', () => {
  const { nodes } = render('![x](notes/none.png) and ![y](Images/fox.png)', { attachments: [] })
  assert.equal(nodes[0].textContent, '![x](notes/none.png) and ![y](Images/fox.png)')
  assert.equal(byClass(nodes, 'reply-figure').length, 0)
  const web = markdown('![a diagram](https://example.com/d.png)', { images: replyImages({ attachments: [fox] }).images })
  assert.equal(web[0].firstChild.tag, 'a', 'a web image is still the link it points at')
})

test('without the images option a text draws as it always did', () => {
  assert.equal(markdown('![a fox](Images/fox.png)')[0].textContent, '![a fox](Images/fox.png)')
  assert.equal(strip('![a fox](Images/fox.png) ok'), 'a fox ok')
  assert.deepEqual(imageRefs('a ![b](c d.png)\n\n- ![e](<f.png>)'), [{ alt: 'b', dest: 'c d.png' }, { alt: 'e', dest: '<f.png>' }])
})

test('files with no place in the words are the rest, drawn under them', () => {
  const doc = { path: 'notes.md', name: 'notes.md', type: 'text/markdown' }
  const pictures = replyImages({ attachments: [fox, owl, doc] })
  assert.deepEqual(pictures.rest(['![a fox](Images/fox.png)']), [owl, doc])
  assert.deepEqual(pictures.rest(['`![a fox](Images/fox.png)` in code is not a place']), [fox, owl, doc])
})

test('a message from before refs: its pictures stay under the words, and its raw record shows none', () => {
  const old = { ...fox }; delete old.refs
  const pictures = replyImages({ attachments: [old] })
  assert.deepEqual(pictures.rest(['![a fox](40/x.png)']), [old])
  const nodes = markdown('Here.\n\n![a fox](/home/x/vault/Images/fox.png)\n\nThere.', { images: pictures.images })
  assert.equal(shape(nodes), 'p. p.')
  assert.equal(nodes.map((n) => n.textContent).join('|'), 'Here.|There.')
})

test('Copy and the plain placeholder have the words, not the syntax', () => {
  const pictures = replyImages({ attachments: [fox] })
  assert.equal(pictures.words('One.\n\n![a fox](Images/fox.png)\n\nTwo ![a fox](Images/fox.png) three. ![x](no.png)'), 'One.\n\nTwo three. ![x](no.png)')
})

test('while it streams: a finished local reference holds a frame, an unfinished one waits', () => {
  const live = replyImages({ attachments: [], streaming: true }, 't:m9', { streaming: true })
  const nodes = markdown('Wait for it\n\n![a fox](Images/fox.png)', { images: live.images })
  assert.equal(shape(nodes), 'p. div.reply-figures')
  assert.equal(byClass(nodes, 'waiting').length, 1)
  assert.equal(live.live('Here comes ![a fo'), 'Here comes ')
  assert.equal(live.live('Here comes ![a fox](Images/fo'), 'Here comes ')
  assert.equal(live.live('Wow!'), 'Wow!')
  assert.equal(live.words('A\n\n![a fox](Images/fox.png)'), 'A')
})

test('the same message read again keeps the same picture nodes', () => {
  const one = render('![a fox](Images/fox.png)', { attachments: [fox] })
  const a = byClass(one.nodes, 'reply-figure')[0]
  const two = render('![a fox](Images/fox.png)', { attachments: [{ ...fox, refs: [...fox.refs] }] })
  const b = byClass(two.nodes, 'reply-figure')[0]
  assert.ok(a && a === b)
  const moved = render('![a fox](Images/fox.png)', { attachments: [{ ...fox, width: 800 }] })
  assert.notEqual(byClass(moved.nodes, 'reply-figure')[0], a, 'a changed file is drawn again')
})

test('a file that cannot be read becomes a calm frame, and a tap does nothing', () => {
  const { nodes } = render('![a fox](Images/fox.png)', { attachments: [fox] }, {})
  const figure = byClass(nodes, 'reply-figure')[0]
  const img = deep(nodes).find((n) => n.tag === 'img')
  img.__on.error({ target: img })
  assert.ok(figure.getAttribute('class').includes('missing'))
  const link = byClass(nodes, 'reply-image')[0]
  assert.equal(link.getAttribute('href'), null)
  assert.equal(all(figure, 'reply-image-missing').length, 1)
})
