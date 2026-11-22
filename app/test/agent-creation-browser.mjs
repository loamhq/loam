import assert from 'node:assert/strict'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { until } from './harness.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const before = process.env.AGENT_CREATION_BEFORE_PUBLIC
const out = process.env.AGENT_CREATION_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const face = await readFile(new URL('../public/icon-192.png', import.meta.url))
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })

const maker = { id: 'scout', name: 'Scout' }
const thread = { id: 't-a0b1c2d3', title: 'A garden companion', default: maker.id, members: [{ persona: maker.id, engine: 'claude-code' }] }
const draft = { draft: 'd-a0b1c2d3', rev: 1, id: 'juniper', name: 'Juniper', ready: true, about: 'A curious companion for a garden in progress.' }
const newThread = { id: 't-f0e1d2c3', title: 'New thread', default: draft.id, members: [{ persona: draft.id, engine: 'claude-code' }] }
const message = (number, author, text) => ({
  id: `m${number}`, kind: author === 'system' ? 'system' : 'message', author, text,
  at: new Date(Date.UTC(2026, 0, 2, 12, number)).toISOString(),
})
const announcement = message(4, 'system', 'Made Juniper (juniper): their first thread is t-c0ffee00')
const later = [
  'Let’s start with the balcony. The pots by the rail get the most morning light.',
  'I’d give the herbs the first two pots and leave the third open for something you discover along the way.',
  'I have basil, mint and a little packet of nasturtiums.',
  'Mint takes up as much room as you give it. There’s a good case for a pot of its own.',
  'I’ll check which pots have a drainage hole on Saturday.',
  'That’s a good place to stop. The rest can wait until you’ve seen the light for yourself.',
  'I’ve added a note to check again after lunch.',
  'We can bring that observation back here when you’re ready.',
  'I’ll take a photo of the pots while the sun is still on them.',
  'A photo from the doorway will help you see which pots have room to spread.',
  'I’ll leave the names off the pots until I’m ready to plant.',
  'Sounds good. Let’s check in after the weekend.',
].map((text, i) => message(i + 5, i % 2 ? maker.id : 'you', text))

function json(res, body, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
function sse(res, event) {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(`data: ${JSON.stringify(event)}\n\n`)
}

async function walk(width, theme) {
  const app = await oneProductFixture({}, before ? { publicDir: before } : {})
  const context = await browser.newContext({
    viewport: { width, height: width === 390 ? 844 : 900 },
    colorScheme: theme, isMobile: width === 390, hasTouch: width === 390, reducedMotion: 'reduce',
  })
  try {
    let agent = null, paginated = false
    const streams = new Set()
    const messages = [
      message(1, 'you', 'Could we make someone to share the garden with?'),
      message(2, maker.id, 'Here’s someone I think you might like to meet.'),
      message(3, 'system', 'Scout drafted a new agent: Juniper (draft d-a0b1c2d3, revision 1)'),
    ]
    const roster = () => agent ? [maker, agent] : [maker]
    const routes = app.routes
    const hostPath = `/v1/threads/${thread.id}`
    routes['GET /v1/agents'] = (_, res) => json(res, { agents: roster() })
    routes['GET /v1/agents/juniper/face'] = (_, res) => { res.writeHead(200, { 'content-type': 'image/png' }); res.end(face) }
    routes['GET /v1/threads'] = (_, res) => json(res, { threads: [thread], agents: roster() })
    routes['GET /v1/threads/events'] = (_, res) => sse(res, { type: 'threads.snapshot', threads: [thread], agents: roster() })
    routes[`GET ${hostPath}`] = (_, res, url) => {
      const earlier = url.searchParams.has('before')
      json(res, { thread, messages: earlier ? messages.slice(0, 4) : paginated ? messages.slice(4) : messages,
        has_more: paginated && !earlier, turn: null })
    }
    routes[`PATCH ${hostPath}`] = (_, res) => json(res, thread)
    routes[`GET ${hostPath}/events`] = (_, res) => {
      streams.add(res)
      res.on('close', () => streams.delete(res))
      sse(res, { type: 'thread.snapshot', members: thread.members, turns: [] })
    }
    const post = m => {
      messages.push(m)
      for (const res of streams) res.write(`data: ${JSON.stringify({ type: 'message.done', message: m })}\n\n`)
    }
    let confirmed = false
    routes['GET /v1/agents/drafts'] = (_, res, url) => json(res, {
      drafts: !confirmed && url.searchParams.get('thread') === thread.id ? [draft] : [],
    })
    routes[`POST /v1/agents/drafts/${draft.draft}/confirm`] = (_, res) => {
      confirmed = true
      agent = { id: draft.id, name: draft.name, face: null }
      post(announcement)
      json(res, { agent, first_thread: 't-c0ffee00' }, 201)
    }
    routes['POST /v1/threads'] = (_, res) => json(res, newThread, 201)
    routes[`GET /v1/threads/${newThread.id}/events`] = (_, res) => sse(res, { type: 'thread.snapshot', members: newThread.members, turns: [] })

    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const location = `${app.url}/threads/${thread.id}`
    const row = id => page.locator(`[data-message-id="${id}"]`)
    const card = row(announcement.id).filter({ has: page.getByText('Agent created', { exact: true }) })
    const last = row(later.at(-1).id)
    const screenshot = async name => {
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame) })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' })
    }

    await page.goto(location)
    await page.getByRole('button', { name: 'Create', exact: true }).click()
    await until(() => streams.size > 0)
    if (before) await page.locator('.agent-draft-result').waitFor()
    else await card.getByRole('button', { name: 'Start a chat with Juniper' }).waitFor()
    for (const m of later) post(m)
    await last.waitFor()
    await page.locator('.tt-stream').evaluate(stream => { stream.scrollTop = stream.scrollHeight })
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme)
    const order = (left, right) => left.evaluate((node, other) => Boolean(node.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING), right)
    if (before) {
      const stuck = page.locator('.agent-draft-result')
      assert.ok(await order(last, await stuck.elementHandle()))
      assert.ok(await stuck.evaluate(node => node.getBoundingClientRect().bottom < innerHeight))
      await screenshot('before-bottom')
      console.log(`${width}px ${theme}: baseline confirmation stayed below later messages`)
      return
    }

    assert.ok(await order(card, await last.elementHandle()))
    assert.ok(await card.evaluate(node => node.getBoundingClientRect().bottom < node.closest('.tt-stream').getBoundingClientRect().top))
    await screenshot('after-bottom')
    await card.evaluate(node => node.scrollIntoView({ block: 'center' }))
    await screenshot('after-history')

    // The same persisted line renders again after a reload and a history read.
    paginated = true
    await page.reload()
    await last.waitFor()
    assert.equal(await card.count(), 0)
    await page.getByRole('button', { name: 'Earlier messages' }).evaluate(button => button.click())
    await card.getByRole('button', { name: 'Start a chat with Juniper' }).waitFor()
    assert.ok(await order(row('m3'), await card.elementHandle()))
    assert.ok(await order(card, await row('m5').elementHandle()))
    assert.equal(await card.count(), 1)

    // Change the roster while this page stays open. The original text and ID stay the same.
    agent = { ...agent, display: 'Juniper Vale', face: { url: '/v1/agents/juniper/face?v=updated' } }
    const action = card.getByRole('button', { name: 'Start a chat with Juniper Vale' })
    await action.waitFor()
    await page.waitForFunction(() => document.querySelector('.tt-agent-created img')?.naturalWidth > 0)
    assert.equal(await card.locator('img').getAttribute('src'), '/api/agents/juniper/face?v=updated')
    await action.click()
    await page.waitForURL(`**/threads/${newThread.id}`)
    const started = app.broker.seen.find(r => r.method === 'POST' && r.path === '/v1/threads')
    assert.deepEqual(JSON.parse(started.body), { persona: draft.id })

    paginated = false
    await page.goto(location)
    await card.getByRole('button', { name: 'Start a chat with Juniper Vale' }).waitFor()
    agent = null
    await card.getByText('No longer here', { exact: true }).waitFor()
    assert.equal(await card.getByRole('button').count(), 0)
    assert.equal(await card.locator('img').count(), 0)
    await card.evaluate(node => node.scrollIntoView({ block: 'center' }))
    await screenshot('after-deleted')
    assert.deepEqual(errors, [])
    console.log(`${width}px ${theme}: creation stayed in history and returned after reload, live display and face updated, chat opened by ID, deletion removed the action`)
  } finally {
    await context.close()
    await app.close()
  }
}

try {
  for (const width of [390, 1440]) for (const theme of ['light', 'dark']) await walk(width, theme)
} finally {
  await browser.close()
}
