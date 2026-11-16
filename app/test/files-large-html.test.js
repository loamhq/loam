import test from 'node:test'
import assert from 'node:assert/strict'
import { createFiles } from '../public/files.js'
import { row, readAnswer } from './fixtures/files-contract.js'
import { LARGE_HTML, LARGE_HTML_BYTES, TEXT_MAX } from './fixtures/large-html.js'
import { fakeBroker, appAgainst, closing, until } from './harness.js'
import { PREVIEW_LIMITS, PREVIEW_POLICY } from '../src/preview.js'

test('a cold HTML panel obtains action capabilities from stat without reading source, and a denied stat blocks opening', async () => {
  const calls = []
  const files = createFiles({ schedule() {}, api: async (method, path) => {
    calls.push(path)
    if (path.startsWith('/files/link')) return { url: '/files/vault/cold.html' }
    if (path.startsWith('/files/live')) return { url: 'https://content.example.org/page.html' }
    if (path.startsWith('/files/stat')) {
      if (path.includes('denied.html')) throw Object.assign(new Error('denied'), { status: 403 })
      return row('vault', 'cold.html')
    }
    throw new Error('unexpected call')
  } })
  await files.openFile('vault', 'cold.html')
  assert.equal(files.st.file.caps.download, true)
  assert.equal(files.st.file.caps.write, true)
  assert.equal(calls.some(p => p.startsWith('/files/read')), false)
  const count = calls.filter(p => p.startsWith('/files/live')).length
  await files.openFile('vault', 'denied.html')
  assert.equal(files.st.file.metadataError, true)
  assert.equal(files.st.file.status, 403)
  assert.equal(calls.filter(p => p.startsWith('/files/live')).length, count)
})

test('HTML preview never needs a text read; an oversized source cannot become an empty saved file', async () => {
  const calls = [], r = row('vault', 'large.html', { size: LARGE_HTML_BYTES })
  const files = createFiles({ schedule() {}, api: async (method, path) => {
    calls.push({ method, path })
    if (path.startsWith('/files/live')) return { url: 'https://content.example.org/page.html' }
    if (path.startsWith('/files/link')) return { url: '/files/vault/large.html' }
    if (path.startsWith('/files/read')) throw Object.assign(new Error('raw text read limit'), { status: 413, body: { size: LARGE_HTML_BYTES, max: TEXT_MAX } })
    throw new Error('unexpected call')
  } })
  await files.openFile(r.root, r.path, r)
  assert.equal(calls.some(c => c.path.startsWith('/files/read')), false)
  assert.equal(files.st.file.loading, false)
  assert.equal(files.st.file.liveUrl, 'https://content.example.org/page.html')
  files.setMode('edit')
  await until(() => !files.st.file.loading)
  assert.equal(files.st.file.status, 413)
  files.edit('')
  await files.save()
  assert.equal(calls.some(c => c.method === 'PUT'), false)
  files.setMode('preview')
  assert.equal(files.st.file.liveUrl, 'https://content.example.org/page.html')
  assert.equal(files.dirty(), false)
})

test('source reads lazily, preserves real text/revision for saving, and a late read cannot replace a different file', async () => {
  let finish, saved
  const files = createFiles({ schedule() {}, timers: { set() {}, clear() {} }, api: async (method, path, body) => {
    if (path.startsWith('/files/live')) return { url: 'https://content.example.org/page.html' }
    if (path.startsWith('/files/link')) return { url: '/files/vault/small.html' }
    if (method === 'PUT') { saved = body; return readAnswer(row('vault', body.path), body.text, 'rev2') }
    return new Promise(resolve => { finish = resolve })
  } })
  await files.openFile('vault', 'small.html', row('vault', 'small.html'))
  files.setMode('edit')
  files.setMode('preview')
  finish(readAnswer(row('vault', 'small.html'), '<h1>Original</h1>', 'rev1'))
  await until(() => files.st.file.textLoaded)
  files.setMode('edit')
  assert.equal(files.st.file.draft, '<h1>Original</h1>')
  files.edit('<h1>Edited</h1>')
  await files.save()
  assert.equal(saved.base_revision, 'rev1')
  assert.equal(saved.text, '<h1>Edited</h1>')
  await files.openFile('vault', 'late.html', row('vault', 'late.html'))
  files.setMode('edit')
  const late = finish
  await files.openFile('vault', 'new.html', row('vault', 'new.html'))
  late(readAnswer(row('vault', 'late.html'), 'old file', 'old'))
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(files.st.file.path, 'new.html')
  assert.equal(files.st.file.textLoaded, false)
})

test('static fallback previews the reported-size HTML, bounds downloads with or without length, and preserves host refusals', async () => {
  assert.equal(Buffer.byteLength(LARGE_HTML), LARGE_HTML_BYTES)
  const json = (res, status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
  const broker = await fakeBroker({
    'GET /v1/files/read': (req, res) => json(res, 413, { error: 'text read limit', size: LARGE_HTML_BYTES, max: TEXT_MAX }),
    'GET /v1/files/download': (req, res, url) => {
      const p = url.searchParams.get('path')
      if (p === 'gone.html') return json(res, 404, { error: 'gone' })
      if (p === 'denied.html') return json(res, 403, { error: 'denied' })
      res.writeHead(200, { 'content-type': 'application/octet-stream', ...(p === 'huge.html' ? { 'content-length': PREVIEW_LIMITS.htmlBytes + 1 } : {}) })
      if (p === 'huge.html' || p === 'chunked.html') return res.end(' '.repeat(PREVIEW_LIMITS.htmlBytes + 1))
      if (p === 'boundary.html') return res.end(' '.repeat(PREVIEW_LIMITS.htmlBytes))
      res.end(LARGE_HTML)
    },
  })
  const app = await appAgainst(broker)
  try {
    const preview = p => fetch(`${app.url}/api/files/preview?root=vault&path=${p}`)
    const res = await preview('large.html')
    assert.equal(res.status, 200)
    assert.equal(res.headers.get('content-security-policy'), PREVIEW_POLICY)
    const html = await res.text()
    assert.equal((html.match(/data:image\/png;base64,/g) ?? []).length, 4)
    assert.equal(PREVIEW_POLICY.includes('allow-scripts'), false, 'event handlers are inert under the static sandbox')
    for (const p of ['huge.html', 'chunked.html']) {
      const limited = await preview(p)
      assert.equal(limited.status, 413)
      assert.match(await limited.text(), /larger than the 16 MB static preview supports/)
    }
    const boundary = await preview('boundary.html')
    assert.equal(boundary.status, 200)
    await boundary.arrayBuffer()
    for (const [p, status] of [['gone.html', 404], ['denied.html', 403]]) assert.equal((await preview(p)).status, status)
    assert.equal((await fetch(`${app.url}/api/files/read?root=vault&path=large.html`)).status, 413, 'the text limit remains in force')
  } finally { await closing(app, broker)() }
})
