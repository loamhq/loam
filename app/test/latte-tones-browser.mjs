// Real page/relay, seven synthetic agents, including explicit palette colours.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.LATTE_TONES_EVIDENCE || path.join(process.env.TMPDIR, 'latte-tones-evidence')
await mkdir(out, { recursive: true })
const app = await oneProductFixture(), results = []
app.f.agents.push({ id: 'iris', name: 'Iris', emoji: null }, { id: 'moss', name: 'Moss', emoji: null })
app.config.avatarColours = { sprout: 'persona-plum', quill: 'persona-teal', iris: 'persona-mauve' }
for (const persona of ['ledger', 'pip', 'iris', 'moss']) {
  const t = await app.f.api('POST', '/threads', { persona })
  await app.f.api('PATCH', `/threads/${t.id}`, { title: `A few notes with ${app.f.agents.find(a => a.id === persona).name}` })
}
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const appearance = mode => Object.fromEntries(['phone', 'desktop'].map(kind => [kind, { mode, size: 100, theme: 'latte', options: { latte: { 'agent-colours': 'latte-tones', 'agent-shapes': 'distinct' } } }]))
const source = await readFile('public/app.js', 'utf8')
const expose = '\nglobalThis.__latte={theme,threads,schedule,state,goSettings,closeSheet};'
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const expected = ['rgb(244, 236, 224)', 'rgb(235, 221, 201)', 'rgb(224, 204, 178)', 'rgb(212, 185, 152)', 'rgb(196, 162, 124)', 'rgb(174, 132, 93)', 'rgb(162, 119, 79)', 'rgb(140, 100, 68)', 'rgb(119, 85, 58)', 'rgb(99, 71, 51)']
const rosterTones = [0, 6, 2, 8, 4, 9, 1]
async function faces(page, selector) {
  const painted = await page.locator(selector).evaluateAll(nodes => nodes.map(n => ({ id: n.dataset.agent, background: getComputedStyle(n).backgroundColor, ink: getComputedStyle(n).color, edge: getComputedStyle(n).boxShadow })))
  assert.ok(painted.length, `no faces at ${selector}`)
  for (const face of painted) {
    const index = app.f.agents.findIndex(a => a.id === face.id)
    assert.ok(index >= 0, JSON.stringify(face))
    const tone = rosterTones[index]
    assert.equal(face.background, expected[tone], `${selector}: ${face.id}`)
    assert.equal(face.ink, tone < 5 ? 'rgb(42, 28, 19)' : 'rgb(255, 248, 238)', `${selector}: ${face.id} ink`)
    assert.notEqual(face.edge, 'none')
  }
  return painted
}
try {
  // Optional counterfactual: only restore the old shared paint function, keeping
  // this tree's colours/fixtures, to isolate the configured-colour bypass.
  if (process.env.LATTE_BASELINE_UI) {
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, appearance('light'))
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    await context.route('**/ui.js', route => route.fulfill({ contentType: 'text/javascript', path: process.env.LATTE_BASELINE_UI }))
    const page = await context.newPage()
    await page.goto(app.url); await page.locator('.tt-personas .blob').first().waitFor()
    await page.waitForFunction(() => document.querySelector('.tt-personas .blob[data-agent="sprout"]').style.background.includes('persona-plum'))
    const face = await page.locator('.tt-personas .blob[data-agent="sprout"]').evaluate(n => getComputedStyle(n).backgroundColor)
    assert.equal(face, await page.evaluate(() => {
      const probe = document.createElement('span'); probe.style.background = 'var(--persona-plum)'; document.body.append(probe); const color = getComputedStyle(probe).backgroundColor; probe.remove(); return color
    }))
    assert.notEqual(face, expected[0])
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: path.join(out, 'configured-bypass-before-1440-light.png'), animations: 'disabled' })
    results.push({ counterfactual: 'old ui.js only', configuredPlum: face, expectedLatte: expected[0] })
    await context.close()
  }
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const values = appearance(mode)
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: mode, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce' })
    await context.route('**/app.js', route => route.fulfill({ contentType: 'text/javascript', body: source + expose }))
    const page = await context.newPage(), errors = []
    page.on('pageerror', e => errors.push(e.message)); page.setDefaultTimeout(10000)
    const shot = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(out, `${name}-${width}-${mode}.png`), animations: 'disabled' })
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.waitForFunction(() => __latte.state.agents.list.length === 7)
      const people = await faces(page, '.tt-personas .blob'), rows = await faces(page, '.tt-row .blob')
      assert.equal(people.length, 7); assert.equal(new Set(people.map(f => f.background)).size, 7)
      await shot('chats')
      await page.locator('.tt-personas').screenshot({ path: path.join(out, `people-${width}-${mode}.png`), animations: 'disabled' })
      await page.getByRole('button', { name: 'Sprout menu', exact: true }).click()
      await page.locator('.tt-persona-card .blob').waitFor()
      const card = await faces(page, '.tt-persona-card .blob')
      await shot('agent-card')
      await page.keyboard.press('Escape')
      await page.evaluate(() => __latte.threads.open('small-details'))
      await page.locator('.tt-speaker .blob').first().waitFor()
      await page.locator('.tt-message .mention[data-face] .blob').first().waitFor()
      const bylines = await faces(page, '.tt-speaker .blob'), mentions = await faces(page, '.tt-message .mention[data-face] .blob')
      const header = await faces(page, '.tt-head .blob')
      await shot('thread')
      // All ten tones and every configuration token, on retained nodes. Their
      // tint is resolved beside the face so a palette wash cannot hide in it.
      const switches = await page.evaluate(async () => {
        const { agentLook, createAgents, setAvatarColours, AGENT_COLOURS } = await import('/sessions.js')
        const { avatarLook, blob } = await import('/ui.js')
        const holder = document.createElement('div'); holder.style.position = 'fixed'; holder.style.left = '-10000px'; document.body.append(holder)
        const roster = createAgents(Array.from({ length: 10 }, (_, i) => ({ id: `paint-${i}`, name: `Paint ${i}` })))
        const rows = []
        for (const token of AGENT_COLOURS) {
          setAvatarColours(Object.fromEntries(roster.list.map(a => [a.id, token])))
          for (const a of roster.list) {
            const look = avatarLook(agentLook(a.id, roster)), face = blob(look, { size: 50 }), tint = document.createElement('span'), reference = document.createElement('span')
            tint.style.background = look.tint
            reference.style.background = `color-mix(in srgb,var(--agent-latte-${look.tone}) 16%,var(--paper-page))`
            holder.append(face, tint, reference)
            rows.push({ token, tone: look.tone, face, tint, reference })
          }
        }
        const read = () => rows.map(r => ({ token: r.token, tone: r.tone, face: getComputedStyle(r.face).backgroundColor, tint: getComputedStyle(r.tint).backgroundColor, reference: getComputedStyle(r.reference).backgroundColor, ink: getComputedStyle(r.face).color }))
        const latte = read()
        document.documentElement.setAttribute('data-option-agent-colours', 'palette')
        const palette = read()
        document.documentElement.setAttribute('data-option-agent-colours', 'latte-tones')
        const restored = read()
        holder.remove(); setAvatarColours({ sprout: 'persona-plum', quill: 'persona-teal', iris: 'persona-mauve' })
        return { latte, palette, restored }
      })
      for (const r of switches.latte) {
        assert.equal(r.face, expected[r.tone]); assert.equal(r.tint, r.reference)
        assert.equal(r.ink, r.tone < 5 ? 'rgb(42, 28, 19)' : 'rgb(255, 248, 238)')
      }
      for (const token of new Set(switches.palette.map(r => r.token))) assert.equal(new Set(switches.palette.filter(r => r.token === token).map(r => r.face)).size, 1, 'configured palette colour restored')
      assert.deepEqual(switches.restored, switches.latte)
      const retained = page.locator('.tt-message .mention[data-face] .blob').first()
      const before = await retained.evaluate(n => { globalThis.__retainedFace = n; return getComputedStyle(n).backgroundColor })
      await page.evaluate(() => document.documentElement.setAttribute('data-option-agent-colours', 'palette'))
      assert.notEqual(await retained.evaluate(n => getComputedStyle(n).backgroundColor), before)
      await page.evaluate(() => document.documentElement.setAttribute('data-option-agent-colours', 'latte-tones'))
      assert.equal(await retained.evaluate(n => n === globalThis.__retainedFace), true)
      assert.equal(await retained.evaluate(n => getComputedStyle(n).backgroundColor), before)
      await page.evaluate(() => __latte.goSettings('agents'))
      await page.locator('.setrow .blob').first().waitFor()
      const settings = await faces(page, '.setrow .blob')
      await shot('settings-agents')
      assert.deepEqual(errors, [])
      results.push({ width, mode, people, rows, card, bylines, mentions, header, settings, configurationTonePairs: switches.latte.length, retainedMention: true, overflow: false, errors })
      console.log(`PASS ${width} ${mode}: seven distinct faces; list/card/header/byline/mentions/settings; 110 configured tone/tint pairs; retained switches; no overflow or page errors`)
    } finally { await context.close() }
  }
  await writeFile(path.join(out, 'browser.json'), JSON.stringify(results, null, 2) + '\n')
} finally { await browser.close(); await app.close() }
