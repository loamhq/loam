// Combined thread sheet, concrete reply models and timestamps; fixture only.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { oneProductFixture } from './one-product-fixture.mjs'
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const sharp = (await import(process.env.LOAM_SHARP || 'sharp')).default
const out = process.env.THREAD_MODEL_LABELS_EVIDENCE
if (!out) throw new Error('Set THREAD_MODEL_LABELS_EVIDENCE')
await mkdir(out, { recursive: true })
const source = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const catalog = JSON.parse(await readFile(new URL('./fixtures/threads/provider-alias-catalog.json', import.meta.url)))
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox'] })
const captures = [], results = [], id = 'a-quieter-week', at = '2026-11-10T22:41:03Z'
const messages = [
  { id: 'm41', author: 'you', kind: 'message', at, text: 'Let’s keep the next step clear. @quill, a second pair of eyes would be useful.' },
  { id: 'm42', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-fable-5-1', at,
    text: 'The plan is ready. I’ve gathered the useful details and kept the next step small. There’s room to make a little progress now, and come back to the rest when you’re ready.', did: [], steps: [] },
  { id: 'm43', author: 'quill', kind: 'message', engine: 'codex', model: 'gpt-6.1-sol', at,
    text: 'A second look agrees: start with the one thing that matters most. The notes are in good shape, and the next step is clear.', did: [], steps: [] },
]
try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
    const app = await oneProductFixture(), errors = []
    for (const provider of catalog.providers) {
      const target = app.f.providers.find((p) => p.id === provider.id)
      if (target) Object.assign(target, structuredClone(provider), { connected: true })
      else app.f.providers.push({ ...structuredClone(provider), connected: true })
    }
    await app.f.api('PATCH', `/threads/${id}`, { engine: { sprout: 'claude-code', quill: 'codex' }, remove: ['scout'] })
    await app.f.api('PATCH', `/threads/${id}`, { model: { sprout: 'claude-fable-5-1', quill: 'gpt-6.1-sol' } })
    app.routes[`GET /v1/threads/${id}`] = async (req, res) => {
      const record = await app.f.api('GET', `/threads/${id}`)
      res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify({ ...record, messages, has_more: false, turn: null }))
    }
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, colorScheme: theme, timezoneId: 'America/Los_Angeles', reducedMotion: 'reduce', isMobile: width === 390, hasTouch: width === 390 })
    await context.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: source + '\nglobalThis.__labels = { threads, schedule };' }))
    const page = await context.newPage(); page.setDefaultTimeout(12000); await page.clock.setFixedTime(new Date(at)); page.on('pageerror', (e) => errors.push(e.message))
    const pill = () => page.locator('.tt-composer button[aria-label="Providers & models"]')
    const settle = () => page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
    const shot = async () => { await settle(); await page.mouse.move(0, 0); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); return page.screenshot({ animations: 'disabled' }) }
    const pair = async (name, leftLabel, rightLabel, left, right) => {
      await left(); const a = await shot(); await right(); const b = await shot()
      const height = a.readUInt32BE(20), file = `${name}-${width}-${theme}.png`, bg = theme === 'dark' ? '#1a1715' : '#e6e1da', ink = theme === 'dark' ? '#eee' : '#222'
      const labels = Buffer.from(`<svg width="${width * 2}" height="32"><text x="14" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${leftLabel.replaceAll('&', '&amp;')}</text><text x="${width + 14}" y="22" fill="${ink}" font-family="sans-serif" font-size="13">${rightLabel.replaceAll('&', '&amp;')}</text></svg>`)
      const dest = path.join(out, file)
      await sharp({ create: { width: width * 2, height: height + 32, channels: 4, background: bg } }).composite([{ input: a, left: 0, top: 32 }, { input: b, left: width, top: 32 }, { input: labels, left: 0, top: 0 }]).png().toFile(dest)
      captures.push({ file, width, theme, sha256: createHash('sha256').update(await readFile(dest)).digest('hex') }); console.log(file)
    }
    try {
      await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
      await page.evaluate((id) => __labels.threads.open(id), id); await page.locator('[data-message-id="m43"]').waitFor()
      await page.locator('.tt-prose-content:not(.tt-prose-plain)').first().waitFor()
      await page.locator('.tt-stream').evaluate((n) => n.scrollTop = 0); await settle()
      for (const [author, provider, model, short] of [['sprout', 'claude-code', 'claude-fable-5-1', 'Fable 5.1'], ['quill', 'codex', 'gpt-6.1-sol', 'GPT-6.1-Sol']]) {
        const meta = page.locator(`.tt-author-run[data-author="${author}"] .tt-author-meta`)
        assert.equal(await meta.locator('.tt-author-model').innerText(), short)
        assert.equal(await meta.locator(`[data-mark="${provider}"]`).count(), 1)
        assert.ok((await meta.getAttribute('title')).includes(model), 'full recorded ID survives in tooltip')
      }
      assert.equal(await pill().locator('.modelbtn-name').innerText(), 'Fable 5.1')
      assert.equal(await pill().locator('[data-mark="claude-code"]').count(), 1)
      assert.ok((await pill().getAttribute('title')).includes('claude-fable-5-1'))
      assert.deepEqual(await page.locator('.tt-reply-footer time').allTextContents(), ['2:41 PM', '2:41 PM', '2:41 PM'])
      assert.equal(await page.locator('.thread-load').count(), 0, 'progressive opening survives the merge')
      await pair('claude-codex', 'Claude reply and default · shared short name', 'Codex reply and default · catalog name', async () => {}, async () => {
        await app.f.api('PATCH', `/threads/${id}`, { default: 'quill' })
        await page.waitForFunction(() => document.querySelector('.tt-composer .modelbtn-name')?.textContent === 'GPT-6.1-Sol')
        assert.equal(await pill().locator('[data-mark="codex"]').count(), 1)
        assert.ok((await pill().getAttribute('title')).includes('gpt-6.1-sol'))
      })
      await pair('sheet', 'Saved member settings · both concrete reply IDs', 'The same Providers & models sheet', async () => {}, async () => {
        await pill().click()
        const sheet = page.getByRole('dialog', { name: 'Providers & models', exact: true }); await sheet.waitFor()
        assert.equal(await sheet.getByRole('button', { name: 'Model for Sprout', exact: true }).innerText(), 'Fable 5.1')
        assert.equal(await sheet.getByRole('button', { name: 'Model for Quill', exact: true }).innerText(), 'GPT-6.1-Sol')
        const bounds = await sheet.boundingBox(); assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= (width === 390 ? 844 : 1000) + 1)
        await sheet.getByRole('button', { name: 'Model for Sprout', exact: true }).click()
        const saved = sheet.getByRole('option').filter({ hasText: 'Saved model' })
        assert.ok((await saved.innerText()).includes('Fable 5.1')); assert.equal((await saved.innerText()).includes('claude-fable-5-1'), false)
        assert.ok((await saved.getAttribute('title')).includes('claude-fable-5-1'))
      })
      await page.getByRole('dialog', { name: 'Providers & models', exact: true }).getByRole('button', { name: 'Done', exact: true }).click()
      await page.getByTitle('Thread details', { exact: true }).click()
      await page.getByRole('button', { name: 'Model for Sprout', exact: true }).click()
      await page.getByRole('dialog', { name: 'Providers & models', exact: true }).waitFor()
      assert.equal(await page.getByRole('listbox', { name: 'Model for Sprout', exact: true }).count(), 1)
      assert.deepEqual(errors, [])
      assert.equal(app.broker.seen.some((r) => r.method === 'POST' && r.path.endsWith('/messages')), false)
      results.push({ width, theme, pass: true, liveCatalogFixture: catalog.live_capture, chipsAndComposerShareLabels: true, exactIdsInTooltips: true, sheetAndDetails: true, replyTimestamps: true, progressiveOpening: true, messagesSent: 0, errors })
    } finally { await context.close(); await app.close() }
  }
} finally { await browser.close() }
await writeFile(path.join(out, 'results.json'), JSON.stringify({ results, captures }, null, 2) + '\n')
console.log(JSON.stringify(results, null, 2))
