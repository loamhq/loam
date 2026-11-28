// Inline visuals in agent replies:
// - fence parsing & ordinary ```html / ```svg code blocks untouched
// - unfinished visual block while streaming shows a calm placeholder, never raw markup
// - fallback on non-drawing surfaces: classic Markdown view in Files, strip()
//   (Activity & thread search), quoteLine(), Sharing standalone Markdown, Read aloud
// - /visual-frame.html HTTP sandbox + CSP nonce headers
// - Save to Files standalone HTML generation and vault path collision handling
import test from 'node:test'
import assert from 'node:assert/strict'
import { installDom } from './fakedom.js'
import { createApp } from '../src/server.js'

installDom()
globalThis.Node ??= Object.getPrototypeOf(document.createElement('div').constructor)
const { markdown, strip } = await import('../public/markdown.js')
const { replyVisuals, saveVisualToFiles } = await import('../public/reply-visuals.js')
const { quoteLine } = await import('../public/thread-replies.js')
const {
  isVisualLang,
  visualTitle,
  visualFallbackLine,
  stripVisualBlocks,
  holdPartialVisualFence,
  buildStandaloneVisualHtml,
  VISUAL_TOKENS,
} = await import('../public/visual-doc.js')
const { renderMarkdown: renderSharedMarkdown } = await import('../../plugins/sharing/page/markdown.js')
const { speakable, speakableBlocks } = await import('../../plugins/_listed/read-aloud/page/speakable.js')

const real = (node) => node?.__keep ?? node
const deep = (nodes) => {
  const out = []
  const walk = (n) => {
    n = real(n)
    if (!n) return
    out.push(n)
    for (const c of n.childNodes ?? []) walk(c)
  }
  ;[].concat(nodes).forEach(walk)
  return out
}

const SAMPLE_TABLE = [
  '```visual Espresso grinders',
  '<table><thead><tr><th>Model</th><th>Price</th></tr></thead>',
  '<tbody><tr><td>Ode Gen 2</td><td>$345</td></tr></tbody></table>',
  '```',
].join('\n')

const SAMPLE_SVG = [
  '```visual',
  '<figure><svg viewBox="0 0 240 80"><rect x="10" y="10" width="120" height="20"><title>July: $72</title></rect></svg><figcaption>Monthly spend</figcaption></figure>',
  '```',
].join('\n')

test('inline visual draws between paragraphs, several per reply, while ordinary html/svg fences stay code blocks', () => {
  const text = [
    'Before the comparison.',
    '',
    SAMPLE_TABLE,
    '',
    'And the chart:',
    '',
    SAMPLE_SVG,
    '',
    '```html',
    '<div class="example">ordinary code</div>',
    '```',
    '',
    '```svg',
    '<svg><circle r="5"/></svg>',
    '```',
  ].join('\n')

  const rv = replyVisuals({ id: 'm1' }, 't1:m1', { streaming: false })
  const nodes = markdown(text, { visuals: rv.visuals })
  const topTags = nodes.map((n) => `${real(n).tag}.${real(n).getAttribute('class') ?? ''}`)
  assert.deepEqual(topTags, [
    'p.',
    'div.reply-visual',
    'p.',
    'div.reply-visual',
    'pre.',
    'pre.',
  ])

  const frames = deep(nodes).filter((n) => n.tag === 'iframe')
  assert.equal(frames.length, 2)
  assert.equal(frames[0].getAttribute('sandbox'), 'allow-scripts')
  assert.equal(frames[0].getAttribute('referrerpolicy'), 'no-referrer')
  assert.match(frames[0].getAttribute('src'), /^\/visual-frame\.html#v=/)
  assert.equal(frames[0].getAttribute('title'), 'Espresso grinders')
  assert.equal(frames[1].getAttribute('title'), 'Monthly spend')

  // Ordinary html and svg code blocks still draw as <pre><code>.
  const codeBlocks = nodes.filter((n) => real(n).tag === 'pre')
  assert.equal(codeBlocks.length, 2)
  assert.match(codeBlocks[0].textContent, /<div class="example">ordinary code<\/div>/)
  assert.match(codeBlocks[1].textContent, /<svg><circle r="5"\/><\/svg>/)
})

test('unfinished visual block while streaming shows a calm placeholder and settles cleanly when closed', () => {
  const partialOpener = 'Here is a breakdown.\n\n```vis'
  const streamingRv = replyVisuals({ id: 'draft:iris', streaming: true }, 't1:reply:iris:1', { streaming: true })
  assert.equal(streamingRv.live(partialOpener), 'Here is a breakdown.\n')
  assert.equal(holdPartialVisualFence('Here is code.\n\n```html\n<b>hi</b>'), 'Here is code.\n\n```html\n<b>hi</b>')

  // Unfinished ```visual fence mid-stream: calm placeholder, zero raw HTML/SVG.
  const midStream = 'Opening paragraph.\n\n```visual Grinder comparison\n<table><tr><td>Ode Gen 2</td>'
  const streamingNodes = markdown(streamingRv.live(midStream), { visuals: streamingRv.visuals })
  assert.equal(streamingNodes.length, 2)
  assert.equal(real(streamingNodes[0]).tag, 'p')
  const placeholder = real(streamingNodes[1])
  assert.equal(placeholder.tag, 'div')
  assert.ok((placeholder.getAttribute('class') ?? '').includes('reply-visual-streaming'))
  assert.equal(placeholder.textContent, 'Drawing Grinder comparison…')
  assert.ok(!placeholder.textContent.includes('<table'), 'never leaks raw markup while streaming')
  assert.equal(deep(streamingNodes).filter((n) => n.tag === 'iframe').length, 0)

  // Once the closing fence arrives (still streaming later prose), the live visual mounts and stays kept.
  const closedStreaming = `${midStream}</tr></table>\n\`\`\`\n\nFollow-up sentence`
  const afterClose = markdown(streamingRv.live(closedStreaming), { visuals: streamingRv.visuals })
  const visualNodeStreaming = real(afterClose[1])
  assert.equal(visualNodeStreaming.tag, 'div')
  assert.ok(!(visualNodeStreaming.getAttribute('class') ?? '').includes('reply-visual-streaming'))
  assert.equal(deep(afterClose).filter((n) => n.tag === 'iframe').length, 1)

  // When message completes with the same viewKey, the exact same kept DOM node is reused.
  const settledRv = replyVisuals({ id: 'm9', streaming: false }, 't1:reply:iris:1', { streaming: false })
  const settled = markdown(`${closedStreaming} finished.`, { visuals: settledRv.visuals })
  assert.equal(real(settled[1]), visualNodeStreaming, 'settled reply reuses the live kept visual node without reload')
})

test('non-drawing surfaces fall back cleanly without raw HTML markup', () => {
  const reply = `Here is the comparison.\n\n${SAMPLE_TABLE}\n\nOde Gen 2 wins for filter coffee.`

  // 1. Classic Markdown view in Files (markdown() without visuals option): collapsed <details> fallback.
  const filesNodes = markdown(reply, { vault: true })
  const fallback = deep(filesNodes).find((n) => n.tag === 'details' && (n.getAttribute('class') ?? '').includes('md-visual-fallback'))
  assert.ok(fallback, 'Files classic Markdown view renders a collapsed visual fallback')
  assert.equal(fallback.hasAttribute('open'), false)
  const summary = deep(fallback).find((n) => n.tag === 'summary')
  assert.match(summary.textContent, /Visual · Espresso grinders/)
  assert.match(summary.textContent, /Shown inline in threads/)

  // 2. strip() (Activity previews and thread search): clean text, even when truncated at 200 chars.
  const stripped = strip(reply)
  assert.equal(stripped, 'Here is the comparison. Visual: Espresso grinders Ode Gen 2 wins for filter coffee.')
  assert.ok(!stripped.includes('<table') && !stripped.includes('<thead>'))

  const truncated = `${SAMPLE_TABLE.slice(0, 80)}`
  const strippedTruncated = strip(truncated)
  assert.equal(strippedTruncated, 'Visual: Espresso grinders')
  assert.ok(!strippedTruncated.includes('<table'))

  // 3. quoteLine() when a reply starts with a visual block.
  const visualOnlyQuote = quoteLine({ id: 'm2', author: 'iris', text: SAMPLE_TABLE })
  assert.equal(visualOnlyQuote, 'Visual: Espresso grinders')
  assert.ok(!visualOnlyQuote.includes('<table'))
  const untitledVisualQuote = quoteLine({ id: 'm3', author: 'iris', text: SAMPLE_SVG })
  assert.equal(untitledVisualQuote, 'Visual: Monthly spend')

  // 4. stripVisualBlocks leaves ordinary html/svg fences alone while stripping visual blocks.
  const mixed = `${SAMPLE_TABLE}\n\n\`\`\`html\n<p>keep me</p>\n\`\`\``
  const cleaned = stripVisualBlocks(mixed)
  assert.match(cleaned, /^Visual: Espresso grinders/)
  assert.match(cleaned, /```html\n<p>keep me<\/p>\n```/)

  // 5. Sharing standalone Markdown renderer.
  const sharedRoot = renderSharedMarkdown(reply)
  const sharedDetails = deep(sharedRoot).find((n) => n.tag === 'details' && (n.getAttribute('class') ?? '').includes('sh-md-visual'))
  assert.ok(sharedDetails, 'Sharing standalone Markdown renders a collapsed visual fallback')
  assert.equal(sharedDetails.hasAttribute('open'), false)
  assert.match(deep(sharedDetails).find((n) => n.tag === 'summary').textContent, /Visual · Espresso grinders/)

  // 6. Read aloud speakable().
  assert.equal(speakable(reply), 'Here is the comparison.\n\nA visual, Espresso grinders, skipped.\n\nOde Gen 2 wins for filter coffee.')
  assert.equal(speakable(SAMPLE_SVG), 'A visual, skipped.')
  const descBlock = speakableBlocks(reply).find((b) => b.description?.language === 'visual')
  assert.ok(descBlock, 'Read aloud exposes visual blocks as description candidates')
  assert.equal(descBlock.description.kind, 'diagram')
  assert.ok(!speakable(reply).includes('<table'))
})

test('fence detection and title extraction reject unrelated languages', () => {
  assert.equal(isVisualLang('visual'), true)
  assert.equal(isVisualLang('VISUAL Chart title'), true)
  assert.equal(isVisualLang('html'), false)
  assert.equal(isVisualLang('svg'), false)
  assert.equal(isVisualLang('visualbasic'), false)
  assert.equal(visualTitle('visual', '<h3>Quarterly numbers</h3><p>x</p>'), 'Quarterly numbers')
  assert.equal(visualFallbackLine('visual', '<svg aria-label="Revenue trend"></svg>'), 'Visual: Revenue trend')
  assert.equal(visualFallbackLine('visual', '<div>plain</div>'), 'Visual')
  assert.ok(VISUAL_TOKENS.includes('--paper-ink') && VISUAL_TOKENS.includes('--signal-crema') && VISUAL_TOKENS.includes('--persona-teal'))
})

test('/visual-frame.html serves opaque sandbox CSP with per-request script nonce and no unsafe-inline script', async (t) => {
  const app = createApp({ broker: { json: async () => ({ status: 503, body: '{}' }) } })
  await new Promise((resolve) => app.server.listen(0, '127.0.0.1', resolve))
  t.after(() => app.server.close())
  const base = `http://127.0.0.1:${app.server.address().port}`

  const res1 = await fetch(`${base}/visual-frame.html`)
  const body1 = await res1.text()
  const csp1 = res1.headers.get('content-security-policy') || ''
  assert.equal(res1.status, 200)
  assert.equal(res1.headers.get('referrer-policy'), 'no-referrer')
  assert.equal(res1.headers.get('x-content-type-options'), 'nosniff')
  assert.equal(res1.headers.get('cache-control'), 'no-store')
  assert.match(csp1, /^sandbox allow-scripts;/)
  assert.match(csp1, /default-src 'none'/)
  assert.match(csp1, /connect-src 'none'/)
  assert.match(csp1, /frame-src 'none'/)
  assert.match(csp1, /img-src data:/)
  assert.match(csp1, /font-src data:/)
  assert.match(csp1, /frame-ancestors 'self'/)
  assert.ok(!/script-src[^;]*unsafe-inline/.test(csp1), 'script-src must never allow unsafe-inline')
  const m1 = /script-src 'nonce-([^']+)'/.exec(csp1)
  assert.ok(m1 && m1[1].length >= 16)
  assert.ok(body1.includes(`<script nonce="${m1[1]}">`))

  const res2 = await fetch(`${base}/visual-frame.html`)
  const csp2 = res2.headers.get('content-security-policy') || ''
  const m2 = /script-src 'nonce-([^']+)'/.exec(csp2)
  assert.ok(m2 && m2[1] !== m1[1], 'every response mints a fresh cryptographic nonce')
})

test('Save to Files produces sanitized standalone themed HTML and handles name collisions', async () => {
  const html = '<style>@import url("https://evil.test/x.css"); .a { color: var(--paper-ink); background: url("https://evil.test/bg.png"); }</style><div class="a" onclick="alert(1)">Hello</div><script>fetch("https://evil.test")</script><img src="https://evil.test/p.png">'
  const standalone = buildStandaloneVisualHtml({
    html,
    title: 'Sample visual',
    tokens: { '--paper-ink': '#1c1b19', '--paper-pane': '#f6f3ee', '--signal-crema': '#b4733a' },
    mode: 'light',
  })
  assert.match(standalone, /<!doctype html>/)
  assert.match(standalone, /--paper-ink: #1c1b19;/)
  assert.ok(!standalone.includes('<script'), 'standalone HTML strips scripts')
  assert.ok(!standalone.includes('onclick'), 'standalone HTML strips inline handlers')
  assert.ok(!standalone.includes('evil.test'), 'standalone HTML strips external URLs')

  const calls = []
  const api = async (method, path, body) => {
    calls.push({ method, path, body })
    if (calls.length === 1) throw Object.assign(new Error('exists'), { status: 409 })
    return { root: 'vault', path: body.path }
  }
  const saved = await saveVisualToFiles({
    html: '<p>Saved card</p>',
    title: 'Grinder comparison',
    api,
    now: new Date('2026-11-27T12:00:00'),
  })
  assert.equal(calls.length, 2)
  assert.equal(calls[0].method, 'POST')
  assert.equal(calls[0].path, '/files/file')
  assert.ok(calls[0].body.path.endsWith('Grinder comparison.html'))
  assert.ok(saved.path.endsWith('Grinder comparison 2.html'))
  assert.match(calls[1].body.text, /Saved card/)
})
