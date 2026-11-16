// Real CSS, relay and browser; synthetic conversations only. Optional tooling
// and screenshots stay outside the repository, as in the other browser drivers.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { fakeBroker, appAgainst, closing } from './harness.js'
import { storedFile, capReason } from './unsent-host.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright-core')
const output = process.env.USER_BUBBLE_EVIDENCE
if (!output) throw new Error('Set USER_BUBBLE_EVIDENCE outside the repository')
const relative = path.relative(fileURLToPath(new URL('..', import.meta.url)), path.resolve(output))
if (!relative || (!relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) throw new Error('Evidence must be outside the repository')
await mkdir(output, { recursive: true })

const rgb = (value) => value.match(/[\d.]+/g).slice(0, 3).map(Number)
const mix = (foreground, background, alpha) => foreground.map((v, i) => v * alpha + background[i] * (1 - alpha))
const luminance = (color) => color.map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((v, channel, i) => v + channel * [.2126, .7152, .0722][i], 0)
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05)
const json = (res, value, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(value)) }
const frame = (event) => `data: ${JSON.stringify(event)}\n\n`
const now = new Date().toISOString()
const record = {
  session_id: 'bubble-fixture', agent: 'test-agent', provider: 'claude-code',
  created: now, last_activity: now, last_event_at: now, state: 'running', live: true,
  status: 'done', title: 'Review the garden plan', last_message: 'The plan is ready to review.',
  pinned: false, archived: false, record_seq: 6, seen_seq: 6,
  background_tasks: 0, pending_permissions: [], turns: [],
}
const reply = (seq, text) => ({ type: 'assistant', seq, message: { id: `reply-${seq}`, content: [{ type: 'text', text }] } })
const events = [
  { type: 'broker.user_message', seq: 1, text: 'Please review the [garden plan](https://example.com/plan) and check `notes.md`.\n\n```sh\ncat notes.md\n```' },
  reply(2, 'The paths are clear and the planting beds have enough space. I suggest keeping the seating area beside the shaded border.'),
  { type: 'result', seq: 3, subtype: 'success' },
  { type: 'broker.user_message', seq: 4, text: 'Here are the notes. Can you use these for the next revision?', attachments: [storedFile] },
  reply(5, 'Yes. I will keep the quieter corner and update the planting list from your notes.'),
  { type: 'result', seq: 6, subtype: 'success' },
]
let heldSend
let sendArrived
const broker = await fakeBroker({
  'GET /v1/agents': (req, res) => json(res, { agents: [{ id: 'test-agent', name: 'Cinder', emoji: null }] }),
  'GET /v1/providers': (req, res) => json(res, { providers: [{ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }] }),
  'GET /v1/limits': (req, res) => json(res, { attachment_max_bytes: 10000000, attachment_types: ['text/plain'] }),
  'GET /v1/sessions': (req, res) => json(res, { sessions: [record], live_count: 1, cap: 4 }),
  'GET /v1/sessions/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n' + frame({ type: 'broker.session', ...record }))
  },
  'GET /v1/sessions/bubble-fixture/events': (req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(': open ping=15000\n\n' + frame({ type: 'broker.session', ...record, window_first_seq: 1, turns_before: 0 }))
    for (const event of events) res.write(frame({ session_id: record.session_id, ...event }))
  },
  'PATCH /v1/sessions/bubble-fixture': (req, res) => json(res, record),
  'POST /v1/sessions/bubble-fixture/messages': (req, res) => { heldSend = () => json(res, { error: capReason }, 429); sendArrived?.() },
})
const app = await appAgainst(broker)
let browser
const results = []
try {
  browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
  for (const { width, theme } of [{ width: 1440, theme: 'light' }, { width: 1440, theme: 'dark' }, { width: 390, theme: 'light' }]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: theme, isMobile: width === 390, hasTouch: width === 390 })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto(app.url)
    await page.locator('.srow').click()
    await page.locator('.say.user .chip.sent').waitFor()
    await page.locator('.thread-load').waitFor({ state: 'detached' })
    await page.evaluate(() => document.fonts.ready)
    const bubbles = page.locator('.say.user')
    assert.equal(await bubbles.count(), 2)
    const measure = (element) => {
      const style = getComputedStyle(element)
      const ground = getComputedStyle(element.closest('.pane') || document.querySelector('.screen.thread')).backgroundColor
      const colors = (selector) => {
        const node = element.querySelector(selector)
        const computed = getComputedStyle(node)
        return { color: computed.color, background: computed.backgroundColor }
      }
      const selection = getComputedStyle(element.querySelector('p'), '::selection')
      return { fill: style.backgroundColor, ink: style.color, opacity: Number(style.opacity), ground,
        link: colors('a'), code: colors('p code'), blockCode: colors('pre code'), block: colors('pre'),
        selection: { color: selection.color, background: selection.backgroundColor } }
    }
    const normal = await bubbles.first().evaluate(measure)
    // The phone's .screen is transparent, with --paper-page supplied by body.
    if (normal.ground === 'rgba(0, 0, 0, 0)') normal.ground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
    const bodyContrast = contrast(rgb(normal.ink), rgb(normal.fill))
    assert.ok(bodyContrast >= 4.5)
    assert.ok(contrast(rgb(normal.link.color), rgb(normal.fill)) >= 4.5)
    const codeTint = mix(rgb(normal.ink), rgb(normal.fill), .16)
    assert.ok(contrast(rgb(normal.code.color), codeTint) >= 4.5)
    assert.ok(contrast(rgb(normal.blockCode.color), mix(rgb(normal.blockCode.color), rgb(normal.block.background), .16)) >= 4.5)
    assert.ok(contrast(rgb(normal.selection.color), rgb(normal.selection.background)) >= 4.5)
    const chip = await page.locator('.say.user .chip.sent').evaluate((node) => ({ color: getComputedStyle(node).color, fill: getComputedStyle(node).backgroundColor }))
    assert.ok(contrast(rgb(chip.color), rgb(chip.fill)) >= 4.5)
    await page.screenshot({ path: path.join(output, `conversation-${width}-${theme}.png`) })
    await page.locator('.say.user .chip.sent').scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(output, `attachment-${width}-${theme}.png`) })
    // Exercise the actual selection paint; then clear it before sending.
    await bubbles.first().locator('p').first().evaluate((node) => { const range = document.createRange(); range.selectNodeContents(node); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range) })
    await page.screenshot({ path: path.join(output, `selection-${width}-${theme}.png`) })
    await page.evaluate(() => getSelection().removeAllRanges())
    const longMessage = 'Please check `notes.md`. ' + Array(16).fill('Please preserve the shaded border and the warm planting palette.').join(' ')
    await page.locator('#draft').fill(longMessage)
    await page.locator('.roundsend:not(:disabled)').waitFor()
    const primary = await page.locator('.roundsend').evaluate((node) => {
      const style = getComputedStyle(node)
      const root = getComputedStyle(document.documentElement)
      return { color: style.color, fill: style.backgroundColor, token: root.getPropertyValue('--latte-espresso').trim() }
    })
    assert.equal(primary.fill, theme === 'dark' ? 'rgb(205, 165, 127)' : 'rgb(74, 58, 46)')
    let arriving = new Promise((resolve) => { sendArrived = resolve })
    await page.locator('.roundsend').click()
    await arriving
    await page.locator('.say.user.pending').waitFor()
    const opacity = await page.locator('.say.user.pending').evaluate((node) => Number(getComputedStyle(node).opacity))
    const pendingContrast = contrast(mix(rgb(normal.ink), rgb(normal.ground), opacity), mix(rgb(normal.fill), rgb(normal.ground), opacity))
    const pendingCodeContrast = contrast(mix(rgb(normal.ink), rgb(normal.ground), opacity), mix(codeTint, rgb(normal.ground), opacity))
    assert.ok(pendingContrast >= 4.5, `pending contrast ${pendingContrast}`)
    assert.ok(pendingCodeContrast >= 4.5, `pending code contrast ${pendingCodeContrast}`)
    await page.screenshot({ path: path.join(output, `pending-${width}-${theme}.png`) })
    assert.ok(heldSend)
    heldSend()
    heldSend = null
    await page.locator('.unsent').waitFor()
    assert.equal(await page.locator('.say.user.pending').count(), 0)
    await page.screenshot({ path: path.join(output, `not-sent-${width}-${theme}.png`) })
    const fade = await page.locator('.say.user.clamped').evaluate((node) => getComputedStyle(node, '::after').backgroundImage)
    assert.ok(fade.includes(normal.fill), 'fold fade differs from bubble fill')
    await page.getByRole('button', { name: 'Show more', exact: true }).click()
    await page.locator('.say.user.clamped').waitFor({ state: 'detached' })
    assert.equal(await page.locator('.say.user.clamped').count(), 0)
    arriving = new Promise((resolve) => { sendArrived = resolve })
    await page.locator('.unsent').getByRole('button', { name: 'Resend', exact: true }).click()
    await page.locator('.say.user.pending').waitFor()
    await arriving
    assert.ok(heldSend)
    heldSend()
    heldSend = null
    await page.locator('.unsent').waitFor()
    await page.locator('.unsent').getByRole('button', { name: 'Edit', exact: true }).click()
    await page.locator('.unsent').waitFor({ state: 'detached' })
    // Edit restores a rich composer: code marks become a code node.
    assert.equal(await page.locator('#draft').innerText(), longMessage.replaceAll('`', ''))
    assert.equal(await page.locator('#draft code').innerText(), 'notes.md')
    assert.equal(await page.locator('.unsent').count(), 0)
    assert.deepEqual(errors, [])
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    assert.equal(overflow, false)
    results.push({ width, theme, normal, bodyContrast, chip, chipContrast: contrast(rgb(chip.color), rgb(chip.fill)),
      linkContrast: contrast(rgb(normal.link.color), rgb(normal.fill)), inlineCodeContrast: contrast(rgb(normal.code.color), codeTint),
      blockCodeContrast: contrast(rgb(normal.blockCode.color), mix(rgb(normal.blockCode.color), rgb(normal.block.background), .16)),
      selectionContrast: contrast(rgb(normal.selection.color), rgb(normal.selection.background)),
      bubbleGroundContrast: contrast(rgb(normal.fill), rgb(normal.ground)), pendingOpacity: opacity, pendingContrast, pendingCodeContrast,
      primary, fade, resendAndEdit: true, horizontalOverflow: overflow, errors })
    await context.close()
  }
  await writeFile(path.join(output, 'checks.json'), JSON.stringify(results, null, 2) + '\n')
  console.log(JSON.stringify(results, null, 2))
} finally {
  await browser?.close()
  await closing(broker, app)()
}
