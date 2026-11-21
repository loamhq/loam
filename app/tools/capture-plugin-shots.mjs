// Screenshots for plugins' pages (2026-11-19): each plugin at work, in the
// demo world (the fixture app's invented agents and threads; no person's
// data), at one window (1120×700, 2x), light and dark, framed alike by
// tools/frame-shot.py. The owner, 11-19: "If it's something that's
// UI-related, it should definitely have screenshots of what the UI looks
// like and how it works." These pictures are published in public plugin
// repositories, which is why nothing here may come from a real install.
//
//   SHOTS_OUT=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> \
//     DEMO_PICTURE=<a png made by Images> PLUGIN_DIRS='{"read-aloud":"<folder>",…}' \
//     node tools/capture-plugin-shots.mjs [scene …]
//
// A scene's plugin is installed into the fixture from its folder (its row as
// the broker lists it, its files where the broker serves them); what its own
// server would answer is stood in for at `/x/<id>/`. Fails on a page error.
import { execFileSync } from 'node:child_process'
import { mkdir, readFile, readdir, stat } from 'node:fs/promises'
import path from 'node:path'
import { filesHost } from '../test/files-host.js'
import { readAnswer, row as fileRow } from '../test/fixtures/files-contract.js'
import { marketFixture } from '../test/market-fixture.mjs'

const out = process.env.SHOTS_OUT
if (!out) throw new Error('Set SHOTS_OUT')
await mkdir(out, { recursive: true })
const dirs = JSON.parse(process.env.PLUGIN_DIRS || '{}')
const ROOT = new URL('../../', import.meta.url).pathname
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
// The online speech proof uses a virtual system microphone fed the demo WAV.
const systemMic = process.env.SHOT_SYSTEM_MIC === '1'
const deviceArgs = systemMic ? [] : ['--use-fake-device-for-media-stream',
  ...(process.env.DEMO_SPEECH ? [`--use-file-for-fake-audio-capture=${process.env.DEMO_SPEECH}`] : [])]
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox', '--enable-speech-dispatcher', '--use-fake-ui-for-media-stream', ...deviceArgs, '--autoplay-policy=no-user-gesture-required'] })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const W = Number(process.env.SHOT_WIDTH || 1120), H = W < 600 ? 844 : 700, CLIP = 640, CLIP_H = 400, AT = '2026-11-19T16:40:00Z', THREAD = 'a-quieter-week'
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const TYPES = { js: 'text/javascript', css: 'text/css', svg: 'image/svg+xml', webp: 'image/webp', png: 'image/png', json: 'application/json', md: 'text/markdown', mp3: 'audio/mpeg', wav: 'audio/wav' }

// A plugin from its folder, as the broker would list it with it on.
function rowOf(dir, id) {
  const py = `import sys, json, pathlib; sys.path.insert(0, ${JSON.stringify(ROOT + 'lib')}); import loam_plugins as P
f = pathlib.Path(${JSON.stringify(dir)}); r = P.read_folder(f, None); m = r['manifest']
print(json.dumps({**m, 'guide': r['guide'], 'has': r['has'], 'declaration': P.declaration(m, r['has'])}))`
  const m = JSON.parse(execFileSync('python3', ['-c', py]))
  const { guide, ...row } = m
  return { row: { ...row, layer: 'yours', folder: `plugins/${id}`, effective: true, on: true, switch: 'plugin', broken: null, warnings: [], files: `/v1/plugins/${id}/files/`, source: { kind: 'link', repo: `maker/loam-${id}`, ref: `v${m.version}`, ref_kind: 'tag', commit: 'c'.repeat(40), url: `https://github.com/maker/loam-${id}/tree/v${m.version}`, via: 'market' } }, guide }
}
async function install(app, id) {
  const dir = dirs[id] ?? path.join(ROOT, 'plugins', id)
  const { row, guide } = rowOf(dir, id)
  app.installed.set(id, row)
  app.routes[`GET /v1/plugins/${id}`] = (req, res) => json(res, { plugin: { ...row, guide }, rows: [row] })
  app.routes[`GET /v1/plugins/${id}/settings`] = (req, res) => json(res, { entries: row.contributes?.['settings.entry'] ?? [], values: {} })
  const walk = async (rel) => {
    for (const name of await readdir(path.join(dir, rel))) {
      const p = rel ? `${rel}/${name}` : name
      if ((await stat(path.join(dir, p))).isDirectory()) { if (!['.git', 'server', 'tests', 'tools'].includes(name)) await walk(p); continue }
      app.routes[`GET /v1/plugins/${id}/files/${p}`] = async (req, res) => { res.writeHead(200, { 'content-type': TYPES[p.split('.').pop()] ?? 'application/octet-stream' }); res.end(await readFile(path.join(dir, p))) }
    }
  }
  await walk('')
  return row
}

// One scene: a fixture app with the thread retitled and given these
// messages, the plugins installed, the page open; `act` does the rest and
// says which shots to take.
async function scene(name, { plugins = [], title, messages = [], files = null, routes = {}, agents = null, storage = {}, setup = null }, act) {
  for (const mode of ['light', 'dark']) {
    const app = await marketFixture({ mode, look: 'latte' }), errors = []
    for (const id of plugins) await install(app, id)
    if (title) await app.f.api('PATCH', `/threads/${THREAD}`, { title })
    const record = await app.f.api('GET', `/threads/${THREAD}`)
    record.thread.unread = 0
    app.routes[`GET /v1/threads/${THREAD}`] = (req, res) => json(res, { ...record, messages: messages.map(m => ({ at: AT, did: [], kind: 'message', ...m })), has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${THREAD}/steps`] = (req, res) => json(res, { error: 'No full record' }, 404)
    if (agents) for (const [r, fn] of Object.entries(agents)) app.routes[r] = fn
    if (setup) setup(app)
    const ctx = await browser.newContext({ ...(systemMic ? { permissions: ['microphone'] } : {}), viewport: { width: W, height: H }, deviceScaleFactor: 2, colorScheme: mode, reducedMotion: 'reduce', timezoneId: 'America/Los_Angeles' })
    await ctx.route('**/app.js', r => r.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__cap={page,state,schedule,threads};' }))
    for (const [pattern, handler] of Object.entries(routes)) await ctx.route(pattern, handler)
    // A person's own device preferences for the scene (localStorage).
    await ctx.addInitScript(values => { for (const [k, v] of Object.entries(values)) localStorage.setItem(k, JSON.stringify(v)) }, storage)
    if (files) await ctx.route('**/api/files/download**', r => { const p = new URL(r.request().url()).searchParams.get('path'); return files[p] ? r.fulfill({ contentType: files[p].type, body: files[p].body }) : r.fulfill({ status: 404, body: '{}' }) })
    const page = await ctx.newPage(); page.setDefaultTimeout(15000)
    page.on('pageerror', e => errors.push(e.message))
    await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
    // One moment, not the whole window (2026-11-19, second pass): a CLIP×CLIP_H
    // box around the element `around` finds (a function's source, run in the
    // page), placed by `align` (center, top or bottom, `pad` from its edge),
    // kept inside the window. At 2x its words read at gallery size.
    const shot = async (label, around = null, { align = 'center', pad = 24, x = 'center' } = {}) => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      await page.waitForTimeout(400)
      const raw = path.join(out, `.${name}-${label}-${mode}.png`)
      let clip
      if (around) {
        const r = await page.evaluate(`(() => { const n = (${around})(); if (!n) return null; const b = n.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height } })()`)
        if (!r) throw new Error(`${name} ${label}: nothing to frame`)
        const fit = (v, max) => Math.round(Math.max(0, Math.min(v, max)))
        const cy = align === 'top' ? r.y - pad : align === 'bottom' ? r.y + r.h + pad - CLIP_H : r.y + r.h / 2 - CLIP_H / 2
        clip = { x: fit(x === 'left' ? r.x - 16 : r.x + r.w / 2 - CLIP / 2, W - CLIP), y: fit(cy, H - CLIP_H), width: CLIP, height: CLIP_H }
      }
      await page.screenshot({ path: raw, animations: 'disabled', ...(clip ? { clip } : {}) })
      execFileSync('python3', [new URL('frame-shot.py', import.meta.url).pathname, raw, path.join(out, `${name}-${label}${mode === 'dark' ? '-dark' : ''}.webp`)], { stdio: 'inherit' })
      execFileSync('rm', ['-f', raw])
    }
    // A message at the top of the thread, held there while the thread settles.
    const showFrom = async (id, offset = 24, inner = null) => {
      for (let i = 0; i < 6; i++) {
        await page.evaluate(([id, offset, inner]) => { const m = document.querySelector(`[data-message-id="${id}"]`), n = inner ? m?.querySelector(inner) ?? m : m, pane = m?.closest('.tt-stream'); if (pane) pane.scrollTop += n.getBoundingClientRect().top - pane.getBoundingClientRect().top - offset }, [id, offset, inner])
        await page.waitForTimeout(250)
      }
      await page.mouse.move(0, 0)
    }
    try {
      await act({ page, app, shot, mode, showFrom, open: async () => { await page.evaluate(id => __cap.threads.open(id), THREAD); await page.locator('.tt-stream').waitFor(); await page.waitForTimeout(800) } })
      if (errors.length) throw new Error(`${name} ${mode}: ${errors.join('; ')}`)
    } finally { await ctx.close(); await app.close() }
  }
}

const SCENES = {}
const only = process.argv.slice(2)
const run = async (name, ...args) => { if (!only.length || only.includes(name)) { await scene(name, ...args); console.log(`PASS ${name}`) } }

// ---------------------------------------------------------------- Images
const picture = process.env.DEMO_PICTURE ? await readFile(process.env.DEMO_PICTURE) : null
const garden = 'Images/2026-11-19 spring garden.png'
const pic = { path: garden, root: 'vault', name: path.basename(garden), type: 'image/png', size: picture?.length ?? 1, width: 1536, height: 768, alt: 'The spring garden, in watercolour', refs: [garden] }
if (picture) {
  await run('images', { plugins: ['images'], title: 'Spring garden', files: { [garden]: { type: 'image/png', body: picture } }, messages: [
    { id: 'm1', author: 'you', text: 'Could you paint the garden plan as a little watercolour? Three beds by the fence, sunflowers at the back.' },
    { id: 'm2', author: 'sprout', engine: 'claude-code', did: [{ label: 'Made an image' }], attachments: [pic],
      text: `Here it is, with the bench by the herbs you mentioned:\n\n![The spring garden, in watercolour](${garden})\n\nIt’s saved in Images if you want it for the planting notes.` },
  ] }, async ({ page, shot, open, showFrom }) => {
    await open()
    await page.waitForFunction(() => { const f = document.querySelector('.reply-figure'); const pane = f?.closest('.tt-stream'); if (pane) pane.scrollTop = pane.scrollHeight; return f && /\bloaded\b/.test(f.className) }, null, { polling: 250 })
    await showFrom('m2', 92, '.reply-figure')
    await shot('reply', "() => document.querySelector('.reply-figure')", { align: 'top', pad: 56 })
    await page.locator('.reply-image').first().click()
    await page.locator('.image-view img').waitFor()
    await page.waitForFunction(() => document.querySelector('.image-view-img')?.complete)
    await shot('full', "() => document.querySelector('.image-view-img')")
  })
}

// ---------------------------------------------------------------- Syncthing
await run('syncthing', { plugins: ['syncthing'], title: 'Notes on my phone', messages: [
  { id: 'm1', author: 'you', text: 'I’d like my notes on my phone too. Its Syncthing ID is MFZWI3D-BONSGYC-YLTMRWG-C43ENR5-QXGZDMM-FZWI3DP-BONSGYY-LTMRWAD.' },
  { id: 'm2', author: 'sprout', engine: 'claude-code', did: [{ label: 'Checked Syncthing on your server' }, { label: 'Paired your phone and shared the vault' }],
    text: 'Done. Your phone is paired, and your vault is shared with it.\n\n**On your phone, in Syncthing:**\n\n1. Accept **this server** when it asks to connect.\n2. Accept the folder **vault**, and choose where it lives.\n\nThe first sync takes a few minutes. Ask me any time how it’s going.' },
  { id: 'm3', author: 'you', text: 'How is it going?' },
  { id: 'm4', author: 'sprout', engine: 'claude-code', did: [{ label: 'Read Syncthing’s status' }],
    text: 'All in step: your laptop and your phone are both up to date, and the last change arrived a minute ago.' },
] }, async ({ page, shot, open, showFrom }) => {
  await open()
  await showFrom('m2', 140)
  await shot('pair', "() => document.querySelector('[data-message-id=\"m2\"]')", { align: 'top', pad: 70 })
  await showFrom('m3', 70)
  await shot('status', "() => document.querySelector('[data-message-id=\"m3\"]')", { align: 'top', pad: 16 })
})

// ---------------------------------------------------------------- Dictation
const GARDEN = [
  { id: 'm1', author: 'you', text: 'Could we plan the spring garden? Somewhere for tomatoes, and a corner for sunflowers.' },
  { id: 'm2', author: 'sprout', engine: 'claude-code', did: [{ label: 'Read your garden notes' }, { label: 'Drafted a plan' }],
    text: 'Gladly. Three beds along the fence get the longest light: tomatoes and beans in the first, herbs by the path, and the sunflowers at the back where they won’t shade anything.\n\nI’ve put the plan in **Garden/plan.md**, with what to sow when.' },
]
const SAID = 'Could we add a little bench by the herbs, facing the sunflowers? Somewhere to sit with a coffee in the morning.'
const dictationRoutes = {
  '**/x/dictation/status': r => r.fulfill({ json: { provider: 'google' } }),
  '**/x/dictation/terms': r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ found: true, path: 'Loam/dictation terms.md', terms: ['Sprout', 'Quill', 'sunflowers'] }) }),
  '**/x/dictation/transcribe': async r => { await new Promise(res => setTimeout(res, 1800)); return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ text: SAID, polish: { ran: true, level: 'light' } }) }) },
}
await run('dictation', { plugins: ['dictation'], title: 'Spring garden', messages: GARDEN, routes: dictationRoutes }, async ({ page, shot, open }) => {
  await open()
  await page.locator('.roundmic').first().click()
  await page.locator('.take-bars').first().waitFor()
  await page.waitForTimeout(6500)
  await page.mouse.move(0, 0)
  await shot('recording', "() => document.querySelector('.roundmic').closest('.composer, form, [class*=compos]') ?? document.querySelector('.roundmic').parentElement.parentElement", { align: 'bottom', pad: 36 })
  await page.locator('.takebtn.keep').first().click()
  await page.waitForFunction(said => [...document.querySelectorAll('textarea, [contenteditable="true"]')].some(n => (n.value ?? n.textContent).includes(said.slice(0, 30))), SAID, { timeout: 15000 })
  await page.waitForTimeout(500)
  await page.mouse.move(0, 0)
  await shot('landed', "() => document.querySelector('.roundmic').closest('.composer, form, [class*=compos]') ?? document.querySelector('.roundmic').parentElement.parentElement", { align: 'bottom', pad: 36 })
  await page.evaluate(() => __cap.page.goSettings('plugins'))
  await page.locator('.plugin-row[aria-label="About Dictation"]').click()
  await page.locator('.plugin-preferences').first().waitFor()
  await page.waitForTimeout(800)
  await page.evaluate(() => { const n = document.querySelector('.plugin-preferences'); const sc = n.closest('.app-modal-body') ?? n.closest('.setmain'); sc.scrollTop += n.getBoundingClientRect().top - sc.getBoundingClientRect().top - 24 })
  await page.waitForTimeout(400)
  await page.mouse.move(0, 0)
  const polishCard = page.locator('.card').filter({ has: page.getByText('Polish', { exact: true }) }).last()
  await polishCard.scrollIntoViewIfNeeded()
  await shot('polish', "() => [...document.querySelectorAll('.card')].find(n => n.querySelector('.kicker')?.textContent === 'Polish')", { x: 'left' })
})

// ---------------------------------------------------------------- Read aloud
const speechFile = process.env.DEMO_SPEECH ? await readFile(process.env.DEMO_SPEECH) : null
const LONG = [
  GARDEN[0],
  { id: 'm2', author: 'sprout', engine: 'claude-code', did: [{ label: 'Read your garden notes' }, { label: 'Drafted a plan' }],
    text: 'Here’s the plan for spring, bed by bed.\n\n**By the fence**, where the light lasts longest: tomatoes on canes at the back, climbing beans in front of them, and a row of lettuce at the edge to pick while the others grow.\n\n**Along the path:** thyme, sage and rosemary, which like it dry and warm, with chives and parsley nearer the tap. Mint goes in a pot, so it stays where it’s put.\n\n**At the back:** the sunflowers, where they won’t shade anything. Sow them in late April and they’ll be taller than the fence by July.\n\nI’ve put it in **Garden/plan.md**, with what to sow when.' },
]
const readRoutes = {
  '**/x/read-aloud/status': r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ speech: true }) }),
  '**/x/read-aloud/speech/lookup': r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'none' }) }),
  '**/x/read-aloud/speech': r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ status: 'ready', id: 'take-1', url: '/x/read-aloud/audio/take-1.wav', duration_s: 12,
    brief_text: 'Three beds: tomatoes and beans by the fence, herbs along the path, sunflowers at the back. Sow the sunflowers in late April.' }) }),
  '**/x/read-aloud/audio/**': r => r.fulfill({ contentType: 'audio/wav', body: speechFile ?? Buffer.alloc(0) }),
}
const profileOf = (row, agent) => ({ agent, identity: { id: agent.id, display: agent.name },
  defaults: { provider: null, model: null, effort: null, effective: { provider: 'claude-code', model: null, effort: null }, applies: '', not_honoured: [] },
  files: {}, schedule: [], status: { threads: [], jobs: [], queued: [], run: null, next_wake: null, box_read: true },
  plugins: [{ id: 'read-aloud', name: 'Read aloud', on: true, fields: row.agent_settings, values: { voice: 'Achird' } }] })
const profileRoutes = app => {
  const row = app.installed.get('read-aloud'), agent = app.f.agents.find(a => a.id === 'sprout')
  app.routes['GET /v1/agents/sprout'] = (req, res) => json(res, profileOf(row, agent))
  app.routes['GET /v1/activity'] = (req, res) => json(res, { items: [], jobs: { slots: [], queue: [], landing: [] }, total: 0, next_offset: null })
  app.routes['GET /v1/schedules'] = (req, res) => json(res, { schedules: [] })
}
if (speechFile) await run('read-aloud', { setup: profileRoutes, plugins: ['read-aloud'], title: 'Spring garden', messages: LONG, routes: readRoutes, storage: { 'loam-ui.speech': { listenFrom: 50, briefFrom: 100, rate: 1 } } }, async ({ page, shot, open, showFrom }) => {
  await open()
  await showFrom('m2', 64)
  const glyph = page.locator('[data-message-id="m2"] .speakglyphs button').first()
  await glyph.waitFor()
  await glyph.click()
  await page.locator('.speakmenu').waitFor()
  await shot('menu', "() => document.querySelector('.speakmenu')", { align: 'bottom', pad: 70 })
  await page.locator('.speakmenu button', { hasText: 'Brief' }).first().click()
  await page.locator('.playerline').first().waitFor()
  await page.waitForTimeout(2500)
  await page.evaluate(() => { const n = document.querySelector('.playerline'), pane = n?.closest('.tt-stream'); if (pane) pane.scrollTop += n.getBoundingClientRect().bottom - pane.getBoundingClientRect().bottom + 140 })
  await page.waitForTimeout(400)
  await page.locator('button', { hasText: 'Show the brief' }).first().click().catch(() => {})
  await page.evaluate(() => { const n = document.querySelector('.playerline'), pane = n?.closest('.tt-stream'); if (pane) pane.scrollTop = pane.scrollHeight })
  await page.waitForTimeout(400)
  await page.mouse.move(0, 0)
  await shot('playing', "() => document.querySelector('.playerline')", { align: 'bottom', pad: 110 })
  // An agent's own voice, on its profile.
  await page.evaluate(() => __cap.page.goProfile('sprout'))
  await page.locator('.profile-tab', { hasText: 'Settings' }).click()
  await page.locator('.voice-picker-trigger').click()
  await page.locator('.agent-voice-picker').waitFor()
  await page.waitForTimeout(500)
  await page.mouse.move(0, 0)
  await shot('voice', "() => document.querySelector('.agent-voice-picker')", { align: 'top', pad: 70 })
})

// Optional online proof. Point SHOT_SYSTEM_MIC=1 and PULSE_SOURCE at a
// virtual microphone playing the garden recording. Chrome produces the
// result and Dictation handles it without a server key.
if (systemMic && only.includes('dictation-spoken')) await run('dictation-spoken', {
  plugins: ['dictation'], title: 'Spring garden', messages: GARDEN,
  routes: { '**/x/dictation/status': r => r.fulfill({ json: { provider: 'browser' } }) },
}, async ({ page, open, shot }) => {
  let posts = 0
  page.on('request', req => { if (new URL(req.url()).pathname === '/x/dictation/transcribe') posts++ })
  await open()
  await page.locator('.roundmic').first().click()
  await page.locator('.take .said').filter({ hasText: /garden/i }).waitFor({ timeout: 30000 })
  await shot('heard')
  await page.locator('.takebtn.keep').first().click()
  await page.waitForFunction(() => [...document.querySelectorAll('textarea,[contenteditable=true]')].some(n => /garden/i.test(n.value ?? n.textContent)))
  await shot('draft')
  if (posts) throw new Error('Browser dictation posted a recording to Loam')
})

// Exercise the same fallback controls in a thread and in Files.
await run('read-aloud-device', { plugins: ['read-aloud'], title: 'Spring garden', messages: LONG,
  setup: app => {
    Object.assign(app.routes, filesHost().routes)
    app.routes['GET /v1/files/read'] = (_req, res) => json(res, readAnswer(fileRow('vault', 'Notes/plan.md'), LONG[1].text, 'sha256:fixture'))
  }, routes: { '**/x/read-aloud/status': route => route.fulfill({ json: { provider: 'browser', speech: false } }) } },
async ({ page, open, shot }) => {
  const sent = []
  page.on('request', req => { if (new URL(req.url()).pathname.startsWith('/x/read-aloud/speech')) sent.push(req.method()) })
  await open()
  await page.locator('[data-message-id="m2"] .speakbtn').click()
  await page.locator('.speakmenu .speech-key-link').waitFor()
  if (await page.locator('.speakmenu').getByText('Brief', { exact: true }).count()) throw new Error('Browser offered Brief')
  await shot('menu')
  await page.locator('.speakmenu .setrow').first().click()
  await page.locator('.playerline').getByText('Browser voice').first().waitFor()
  await shot('player')
  await page.evaluate(() => __cap.page.goFiles('vault', 'Notes/plan.md'))
  await page.locator('.fs-speak').click()
  await page.locator('.file-speech-action').waitFor()
  await shot('files')
  await page.locator('.file-speech-action').click()
  await page.locator('.fs-player').getByText('Browser voice').first().waitFor()
  if (sent.length) throw new Error('Browser playback contacted the server')
})

await run('dictation-device', { plugins: ['dictation'], title: 'Spring garden', messages: GARDEN,
  routes: { '**/x/dictation/status': route => route.fulfill({ json: { provider: 'browser' } }) } },
async ({ page, open, shot }) => {
  await open()
  await page.locator('.roundmic').first().click()
  await page.getByText(/This browser could not recognise speech|This browser has no speech recognition|This browser needs microphone permission/).first().waitFor()
  await shot('microphone')
})

// Speech provider settings and the shared-key form in a neutral demo install.
for (const provider of ['google', 'openai', 'browser']) {
  for (const plugin of ['dictation', 'read-aloud']) {
    let selected = provider
    const label = { google: 'Using Google', openai: 'Using OpenAI', browser: 'Using this browser' }[provider]
    const name = `${plugin}-${provider}`
    await run(name, { plugins: [plugin], title: 'Spring garden', messages: LONG, setup: plugin === 'read-aloud' ? profileRoutes : null,
      routes: { ...dictationRoutes, ...readRoutes, [`**/x/${plugin}/status`]: route => route.fulfill({ json: { provider: selected, speech: selected !== 'browser' } }) } },
    async ({ page, shot }) => {
      await page.evaluate(id => __cap.page.goSettings('plugins', id), plugin)
      const section = page.locator('.speech-provider')
      await section.getByText(label, { exact: true }).waitFor()
      await section.scrollIntoViewIfNeeded()
      await shot('settings')
      await section.locator('button').click()
      await page.getByRole('dialog', { name: 'Use a Gemini key' }).waitFor()
      await shot('guide')
      if (provider === 'browser') {
        await page.route('**/api/keys/google', route => {
          const key = route.request().postDataJSON().values.GEMINI_API_KEY
          if (route.request().method() !== 'PUT' || key !== 'fixture-key') throw new Error('Expected a shared Settings save')
          selected = 'google'
          return route.fulfill({ json: { checked: { ok: true } } })
        })
        await page.getByLabel('Gemini API key', { exact: true }).fill('fixture-key')
        await page.getByRole('button', { name: 'Check and save' }).click()
        await page.getByText('Google accepted your key.', { exact: false }).waitFor()
        await section.getByText('Using Google', { exact: true }).waitFor({ state: 'attached' })
      }
      await page.getByRole('button', { name: 'Close', exact: true }).last().click()
      selected = provider
      if (provider === 'openai' && plugin === 'read-aloud') {
        await page.evaluate(() => __cap.page.goProfile('sprout'))
        await page.locator('.profile-tab', { hasText: 'Settings' }).click()
        await page.locator('.voice-picker-trigger').click()
        await page.locator('.agent-voice-picker').waitFor()
        await shot('voices')
      }
    })
  }
}
await browser.close()
