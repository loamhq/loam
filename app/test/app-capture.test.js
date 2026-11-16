import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import http from 'node:http'
import { once } from 'node:events'
import { captureAppPage } from '../src/app-capture.js'
async function directory(t) { const dir = await mkdtemp(path.join(tmpdir(), 'app-capture-')); t.after(() => rm(dir, { recursive: true, force: true })); return dir }

test('capture child receives no server credentials, times out and removes its temporary profile', async t => {
  const dir = await directory(t), executable = path.join(dir, 'browser')
  const previous = process.env.BROKER_TOKEN; process.env.BROKER_TOKEN = 'fixture-not-a-real-secret'
  t.after(() => { if (previous === undefined) delete process.env.BROKER_TOKEN; else process.env.BROKER_TOKEN = previous })
  await writeFile(executable, `#!${process.execPath}\nimport('node:fs').then(fs=>{fs.writeFileSync(${JSON.stringify(path.join(dir, 'env.json'))},JSON.stringify(process.env));setInterval(()=>{},1000)})`, { mode: 0o700 })
  await assert.rejects(captureAppPage({ url: 'https://example.org/', dir, executable, timeoutMs: 2000 }), /timed out/)
  const env = JSON.parse(await readFile(path.join(dir, 'env.json'), 'utf8'))
  assert.equal(env.BROKER_TOKEN, undefined); assert.equal(env.BROKER_URL, undefined); assert.equal(env.GEMINI_API_KEY, undefined)
  assert.match(env.TMPDIR, /capture-/)
  assert.ok((await readdir(dir)).every(name => !name.startsWith('capture-')))
})
test('real Chromium captures the page top as an 800 × 500 JPEG and refuses a sign-in redirect or HTTP error', {
  skip: !process.env.APP_PREVIEW_CHROME && 'Set APP_PREVIEW_CHROME to exercise the real capture browser.',
}, async t => {
  const dir = await directory(t), seen = []
  const site = http.createServer((req, res) => {
    seen.push(req.headers)
    if (req.url === '/private') { res.writeHead(302, { location: 'http://localhost:1/sign-in' }); return res.end() }
    if (req.url === '/missing') { res.writeHead(404); return res.end('No page') }
    if (req.url === '/blank-canvas') return res.end('<!doctype html><style>html,body{margin:0;background:#151515}canvas{width:100vw;height:100vh}</style><canvas width="1280" height="800"></canvas>')
    if (req.url === '/black-hud') return res.end('<!doctype html><style>html,body{margin:0;background:#000;color:#fff;font:10px sans-serif}canvas{position:absolute;inset:0;width:100vw;height:100vh}small{position:absolute;top:4px;left:4px}</style><canvas width="1280" height="800"></canvas><small>Starting…</small>')
    res.end('<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#dae5da;font:40px sans-serif}h1{padding:70px}</style><h1>A little garden</h1>')
  })
  site.listen(0, '127.0.0.1'); await once(site, 'listening'); t.after(() => { site.closeAllConnections(); site.close() })
  const url = `http://127.0.0.1:${site.address().port}`
  const options = { dir, executable: process.env.APP_PREVIEW_CHROME, timeoutMs: 7000 }
  const jpeg = await captureAppPage({ ...options, url })
  assert.equal(jpeg[0], 255); assert.equal(jpeg[1], 216); assert.ok(jpeg.length > 1000)
  // SOF dimensions in the actual JPEG, without an image package.
  let dimensions
  for (let at = 2; at < jpeg.length;) {
    if (jpeg[at] !== 255) break
    const marker = jpeg[at + 1], length = jpeg.readUInt16BE(at + 2)
    if ([0xc0, 0xc1, 0xc2].includes(marker)) { dimensions = [jpeg.readUInt16BE(at + 7), jpeg.readUInt16BE(at + 5)]; break }
    at += length + 2
  }
  assert.deepEqual(dimensions, [800, 500])
  for (const path of ['/private', '/missing']) await assert.rejects(captureAppPage({ ...options, url: url + path }), /navigation|available app page/)
  await assert.rejects(captureAppPage({ ...options, url: url + '/blank-canvas' }), /was empty/)
  await assert.rejects(captureAppPage({ ...options, url: url + '/black-hud' }), /was empty/)
  assert.ok(seen.every(headers => !headers.authorization && !headers.cookie))
  assert.deepEqual(await readdir(dir), [])
})
test('real Chromium photographs rendered content without waiting for a never-ending load or animation callback', {
  skip: !process.env.APP_PREVIEW_CHROME && 'Set APP_PREVIEW_CHROME to exercise bounded rendering.',
}, async t => {
  const dir = await directory(t), requested = []
  const site = http.createServer((req, res) => {
    requested.push(req.url)
    if (req.url === '/never.png') return
    res.end('<!doctype html><style>body{margin:0;background:#d5e5dd;color:#274d48;font:40px sans-serif;padding:90px}h1{font-size:70px}</style><h1>Already here.</h1><p>A moving page does not need to finish.</p>' + (req.url === '/loading' ? '<img src="/never.png">' : '<script>window.requestAnimationFrame=()=>0</script>'))
  })
  site.listen(0, '127.0.0.1'); await once(site, 'listening')
  t.after(() => { site.closeAllConnections(); site.close() })
  for (const route of ['/loading', '/animation']) {
    const started = performance.now()
    const jpeg = await captureAppPage({ url: `http://127.0.0.1:${site.address().port}${route}`, dir, executable: process.env.APP_PREVIEW_CHROME, timeoutMs: 12000 })
    assert.ok(jpeg.length > 1000)
    const ms = Math.round(performance.now() - started)
    assert.ok(ms < 10000, `${route} exceeded the partial-render budget`)
    t.diagnostic(JSON.stringify({ page: route, state: 'ok', ms }))
    if (process.env.APP_CAPTURE_EVIDENCE) {
      await mkdir(process.env.APP_CAPTURE_EVIDENCE, { recursive: true })
      await writeFile(path.join(process.env.APP_CAPTURE_EVIDENCE, route.slice(1) + '.jpg'), jpeg)
    }
  }
  assert.ok(requested.includes('/never.png'))
  assert.deepEqual(await readdir(dir), [])
})
test('a continuously animated WebGL canvas is photographed when it renders, otherwise its blank image falls back', {
  skip: !process.env.APP_PREVIEW_CHROME && 'Set APP_PREVIEW_CHROME to exercise native WebGL.',
}, async t => {
  const dir = await directory(t); let graphics
  const site = http.createServer((req, res) => {
    if (req.url.startsWith('/context?')) { graphics = req.url.endsWith('true'); return res.end() }
    res.end(`<!doctype html><style>html,body{margin:0;background:#000}canvas{width:100vw;height:100vh}</style><canvas width="1280" height="800"></canvas><script>
      const gl=document.querySelector('canvas').getContext('webgl');fetch('/context?available='+Boolean(gl));
      if(gl){function draw(){gl.disable(gl.SCISSOR_TEST);gl.clearColor(.18,.4,.65,1);gl.clear(gl.COLOR_BUFFER_BIT);gl.enable(gl.SCISSOR_TEST);gl.scissor(640,0,640,800);gl.clearColor(.65,.4,.18,1);gl.clear(gl.COLOR_BUFFER_BIT);requestAnimationFrame(draw)}draw()}
    </script>`)
  })
  site.listen(0, '127.0.0.1'); await once(site, 'listening')
  t.after(() => { site.closeAllConnections(); site.close() })
  const started = performance.now()
  let jpeg, error
  try { jpeg = await captureAppPage({ url: `http://127.0.0.1:${site.address().port}/`, dir, executable: process.env.APP_PREVIEW_CHROME, timeoutMs: 12000 }) }
  catch (caught) { error = caught }
  assert.equal(typeof graphics, 'boolean', 'the page reported its actual WebGL context availability')
  if (graphics) {
    assert.ifError(error); assert.ok(jpeg.length > 1000)
    if (process.env.APP_CAPTURE_EVIDENCE) {
      await mkdir(process.env.APP_CAPTURE_EVIDENCE, { recursive: true })
      await writeFile(path.join(process.env.APP_CAPTURE_EVIDENCE, 'webgl.jpg'), jpeg)
    }
  } else assert.match(error?.message ?? '', /was empty/)
  t.diagnostic(JSON.stringify({ webgl: graphics, state: jpeg ? 'ok' : 'fallback', ms: Math.round(performance.now() - started) }))
  assert.deepEqual(await readdir(dir), [])
})
test('an OOM score starts the browser through choom, so the browser and its children carry it', {
  skip: !process.env.APP_PREVIEW_OOM_CHECK && 'Set APP_PREVIEW_OOM_CHECK=1 on a Linux host with util-linux to exercise choom.',
}, async t => {
  const dir = await directory(t), executable = path.join(dir, 'browser')
  await writeFile(executable, `#!${process.execPath}\nimport('node:fs').then(fs=>{fs.writeFileSync(${JSON.stringify(path.join(dir, 'oom'))},fs.readFileSync('/proc/self/oom_score_adj','utf8'));setInterval(()=>{},1000)})`, { mode: 0o700 })
  await assert.rejects(captureAppPage({ url: 'https://example.org/', dir, executable, timeoutMs: 1500, oomScoreAdj: 800 }), /timed out/)
  assert.equal((await readFile(path.join(dir, 'oom'), 'utf8')).trim(), '800')
})
