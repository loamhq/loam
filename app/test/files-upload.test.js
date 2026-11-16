// Files upload: the relay, against a broker that keeps the bytes it was sent
// as bytes. A real multipart body made by Node's own `FormData`, passed
// through untouched; the address checked here before anything is relayed; the
// body bounded without holding more than the cap; and every host answer —
// the stored row with its renamed name, and each refusal — handed back as the
// host gave it. The page's side is in `files-ui.test.js` and
// `files-desktop.test.js`.

import test from 'node:test'
import http from 'node:http'
import net from 'node:net'
import { once } from 'node:events'
import assert from 'node:assert/strict'

import { appAgainst, closing, TOKEN } from './harness.js'
import { FILES_UPLOAD_MAX_BYTES, FILES_UPLOAD_BODY, filesUploadQuery } from '../src/server.js'
import { uploadRefusal, uploadAccept, createFiles } from '../public/files.js'
import { UPLOAD_ANSWER_REAL, UPLOAD_REFUSALS, uploadAnswer, limitsAnswer, PNG } from './fixtures/files-contract.js'

const seen = []
let answer = null
const broker = http.createServer(async (req, res) => {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const body = Buffer.concat(chunks)
  const url = new URL(req.url, 'http://x')
  seen.push({ method: req.method, path: url.pathname, search: url.search, auth: req.headers.authorization, type: req.headers['content-type'], body })
  if (req.headers.authorization !== `Bearer ${TOKEN}`) {
    res.writeHead(401, { 'content-type': 'application/json' })
    return res.end(JSON.stringify({ error: 'bearer token required' }))
  }
  const [status, out] = answer ? answer(url, body, req) : [201, UPLOAD_ANSWER_REAL]
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(out))
})
broker.listen(0, '127.0.0.1')
await once(broker, 'listening')
const brokerRef = { server: broker, url: `http://127.0.0.1:${broker.address().port}` }
const app = await appAgainst(brokerRef)

test.after(closing(brokerRef, app))
test.beforeEach(() => {
  seen.length = 0
  answer = null
})

const upload = (search, form) => fetch(`${app.url}/api/files/upload${search}`, { method: 'POST', body: form })

test('the multipart envelope reaches the host byte for byte, with the token and the address', async () => {
  // Every byte value, so nothing could have been read as text on the way.
  const bytes = Buffer.concat([PNG, Buffer.from(Array.from({ length: 256 }, (_, i) => i)), Buffer.from([0xff, 0xfe, 0x00, 0x0d, 0x0a])])
  const form = new FormData()
  form.append('file', new File([bytes], 'IMG 2041.png', { type: 'image/png' }))
  const res = await upload('?root=vault&path=Uploads', form)
  assert.equal(res.status, 201)
  assert.deepEqual(await res.json(), UPLOAD_ANSWER_REAL)
  assert.equal(seen.length, 1)
  const call = seen[0]
  assert.equal(call.path, '/v1/files/upload')
  assert.equal(call.search, '?root=vault&path=Uploads')
  assert.equal(call.auth, `Bearer ${TOKEN}`)
  assert.match(call.type, /^multipart\/form-data; boundary=/)
  // What the host reads out of the envelope is exactly what was put in it.
  const parsed = await new Response(call.body, { headers: { 'content-type': call.type } }).formData()
  const file = parsed.get('file')
  assert.equal(file.name, 'IMG 2041.png')
  assert.deepEqual(Buffer.from(await file.arrayBuffer()), bytes)
  assert.deepEqual([...parsed.keys()], ['file'])
})

test('the vault top is `path=` sent empty, and a folder with spaces and non-ASCII is encoded once', async () => {
  const form = () => {
    const f = new FormData()
    f.append('file', new File(['x'], 'a.txt'))
    return f
  }
  assert.equal((await upload('?root=vault', form())).status, 201)
  assert.equal(seen.at(-1).search, '?root=vault&path=')
  assert.equal((await upload(`?root=vault&path=${encodeURIComponent('From app/Été notes')}`, form())).status, 201)
  assert.equal(seen.at(-1).search, `?root=vault&path=${encodeURIComponent('From app/Été notes')}`)
  // Nothing but root and path goes on.
  assert.equal((await upload('?root=vault&path=A&overwrite=1&name=x', form())).status, 201)
  assert.equal(seen.at(-1).search, '?root=vault&path=A')
})

test('a taken name comes back as the host stored it, and that is what the page is given', async () => {
  answer = () => [201, uploadAnswer('vault', 'Notes', 'IMG_2041 2.jpg', 'IMG_2041.jpg', 3)]
  const form = new FormData()
  form.append('file', new File(['abc'], 'IMG_2041.jpg', { type: 'image/jpeg' }))
  const body = await (await upload('?root=vault&path=Notes', form)).json()
  assert.equal(body.path, 'Notes/IMG_2041 2.jpg')
  assert.equal(body.name, 'IMG_2041 2.jpg')
  assert.equal(body.link, '/files/vault/Notes/IMG_2041%202.jpg')
  assert.equal(body.requested_name, 'IMG_2041.jpg')
  assert.equal(body.renamed, true)
})

test('an address with traversal, a control character or no root is refused here and never relayed', async () => {
  for (const search of ['?root=vault&path=..', '?root=vault&path=Notes%2F..%2F..', '?root=vault&path=.%2Fx', '?root=vault&path=a%5Cb', '?root=vault&path=a%00b', '?root=vault&path=%2Fetc', '?path=Notes', '?root=..&path=', '?root=vault%2Fx&path=']) {
    const form = new FormData()
    form.append('file', new File(['x'], 'a.txt'))
    const res = await upload(search, form)
    assert.equal(res.status, 400, search)
    assert.equal(typeof (await res.json()).error, 'string')
  }
  assert.equal(seen.length, 0, 'nothing reached the host')
  assert.deepEqual(filesUploadQuery(new URL('http://x/?root=spaces&path=a/b')), { search: '?root=spaces&path=a%2Fb' })
})

test('every refusal the host gives is handed back with its status and its words', async () => {
  for (const [name, [status, body]] of Object.entries(UPLOAD_REFUSALS)) {
    answer = () => [status, body]
    const form = new FormData()
    form.append('file', new File(['x'], 'a.txt'))
    const res = await upload('?root=vault&path=15_CONVERSATIONS', form)
    assert.equal(res.status, status, name)
    assert.deepEqual(await res.json(), body, name)
  }
})

test('a body over the cap and its envelope is drained, answered 413 with a sentence, and never relayed', async () => {
  assert.equal(FILES_UPLOAD_BODY, FILES_UPLOAD_MAX_BYTES + 8 * 1024)
  const form = new FormData()
  form.append('file', new File([Buffer.alloc(FILES_UPLOAD_BODY + 1)], 'big.bin'))
  const res = await upload('?root=vault&path=Notes', form)
  assert.equal(res.status, 413)
  const body = await res.json()
  assert.match(body.error, /10 MiB/)
  assert.equal(body.max, FILES_UPLOAD_MAX_BYTES)
  assert.equal(seen.length, 0)
})

test('a file at the cap goes through: the bound leaves room for the envelope', async () => {
  answer = (url, body) => [201, uploadAnswer('vault', 'Notes', 'exact.bin', 'exact.bin', body.length)]
  const form = new FormData()
  form.append('file', new File([Buffer.alloc(FILES_UPLOAD_MAX_BYTES, 7)], 'exact.bin'))
  const res = await upload('?root=vault&path=Notes', form)
  assert.equal(res.status, 201)
  assert.equal(seen.length, 1)
  assert.ok(seen[0].body.length > FILES_UPLOAD_MAX_BYTES && seen[0].body.length <= FILES_UPLOAD_BODY)
})

test('an oversized body sent slowly on a raw socket gets its 413, not a reset', async () => {
  const port = new URL(app.url).port
  const sock = net.connect(port, '127.0.0.1')
  await once(sock, 'connect')
  const size = FILES_UPLOAD_BODY + 64 * 1024
  sock.write(`POST /api/files/upload?root=vault&path=Notes HTTP/1.1\r\nHost: x\r\nContent-Type: multipart/form-data; boundary=b\r\nContent-Length: ${size}\r\nConnection: close\r\n\r\n`)
  const chunk = Buffer.alloc(256 * 1024)
  let sent = 0
  while (sent < size) {
    const n = Math.min(chunk.length, size - sent)
    if (!sock.write(chunk.subarray(0, n))) await once(sock, 'drain')
    sent += n
  }
  const got = []
  sock.on('data', (c) => got.push(c))
  await once(sock, 'end')
  const reply = Buffer.concat(got).toString('utf8')
  assert.match(reply, /^HTTP\/1\.1 413 /)
  assert.match(reply, /over the 10 MiB/)
  assert.equal(seen.length, 0)
})

test('the page refuses before sending only on the host’s own numbers', () => {
  const limits = { max: 10485760, types: limitsAnswer().files_upload_extensions.map((e) => e.replace(/^\./, '')) }
  assert.equal(uploadRefusal({ name: 'IMG.HEIC', size: 10 }, limits), '')
  assert.match(uploadRefusal({ name: 'clip.mov', size: 10 }, limits), /not a type Files takes/)
  assert.match(uploadRefusal({ name: 'big.png', size: 10485761 }, limits), /at most 10\.0 MB/)
  assert.equal(uploadRefusal({ name: 'clip.mov', size: 1e9 }, { max: null, types: null }), '', 'with no word from the host, nothing is refused here')
  assert.equal(uploadAccept(limits), limits.types.map((t) => `.${t}`).join(','))
  assert.equal(uploadAccept({ max: null, types: null }), '')
})

test('several files go one request each; a failure is that file’s alone and a retry sends only it', async () => {
  const sent = []
  let fail = 1
  const files = createFiles({
    api: async () => ({ folder: null, entries: [], total: 0, next_cursor: null }),
    schedule: () => {},
    sendForm: async (href, form) => {
      const f = form.get('file')
      sent.push([href, f.name])
      if (f.name === 'b.pdf' && fail-- > 0) throw Object.assign(new Error('The host did not answer.'), { status: 502 })
      if (f.name === 'c.md') throw Object.assign(new Error('This folder is read-only.'), { status: 403 })
      return uploadAnswer('vault', 'Notes', f.name, f.name, f.size)
    },
  })
  files.setUploadLimits(limitsAnswer())
  files.st.root = 'vault'
  files.st.path = 'Notes'
  await files.upload([new File(['a'], 'a.png'), new File(['b'], 'b.pdf'), new File(['c'], 'c.md'), new File(['d'], 'd.exe')])
  const items = files.st.sheet.items
  assert.deepEqual(items.map((i) => i.state), ['done', 'failed', 'failed', 'failed'])
  assert.deepEqual(sent.map((s) => s[1]), ['a.png', 'b.pdf', 'c.md'], 'the refused type never left')
  assert.ok(sent.every(([href]) => href === '/api/files/upload?root=vault&path=Notes'))
  assert.deepEqual(items.map((i) => i.retry), [false, true, false, false], 'only a failure that could go differently is retried')
  await files.retryUpload(items[1])
  assert.equal(items[1].state, 'done')
  assert.deepEqual(sent.map((s) => s[1]), ['a.png', 'b.pdf', 'c.md', 'b.pdf'])
})
