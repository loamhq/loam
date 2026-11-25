// Real Chromium verification of the agent draft card experience across phone
// (390px) and desktop (1440px), light and dark:
//   1. Draft appears -> expand -> send message -> sent message, thinking, and reply stay visible.
//   2. Revision arrives -> card updates in place and collapses -> one tap expands cleanly.
//   3. Quiet close hides card (without deleting draft) -> persists across reload -> next revision returns it collapsed.
//   4. Making the agent (even while card is hidden) ends the card and shows the Agent created card.
//   5. Ordinary thread with no draft card scrolls identically to before.
//   6. Short viewport (mobile keyboard height) still keeps the newest message in view.
//
// Run:
//   LOAM_PLAYWRIGHT=<playwright index.mjs> LOAM_CHROME=<chromium> node test/agent-draft-card-browser.mjs
// Optional:
//   AGENT_DRAFT_EVIDENCE=<dir>         save PNG frames
//   AGENT_DRAFT_BEFORE_PUBLIC=<dir>    run baseline capture against previous public/
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { until } from './harness.js'
import { oneProductFixture } from './one-product-fixture.mjs'
import { auditControls, assertControls } from './control-style-guard.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const before = process.env.AGENT_DRAFT_BEFORE_PUBLIC
const out = process.env.AGENT_DRAFT_EVIDENCE
if (out) await mkdir(out, { recursive: true })
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

const maker = { id: 'scout', name: 'Scout' }
const draftThread = {
  id: 't-d1a2b3c4',
  title: 'Drafting a librarian agent',
  default: maker.id,
  members: [{ persona: maker.id, engine: 'claude-code' }],
}
const ordinaryThread = {
  id: 't-0rd1n4r7',
  title: 'Weekend garden notes',
  default: maker.id,
  members: [{ persona: maker.id, engine: 'claude-code' }],
}

const longSoul = (note) => `# Alder

A calm, thoughtful librarian who keeps your reading lists, book notes, and research shelf tidy.

${note}

## How Alder works

- Remembers which books you loved and why, without turning reading into a chore
- Organises highlights by theme and links related notes together gently
- Keeps a short "next up" stack of three picks rather than an overwhelming backlog
- Notes who recommended each book and what mood it fits best

## Tone and habits

- Brief, warm, and unhurried — never preachy about unfinished books
- Quotes passages accurately with page or chapter references when you share them
- Asks one clear clarifying question when a reading goal is broad
- Respects quiet stretches when you are between books

## Reading shelves

- Currently reading, with a quick place for stray quotes and half-formed thoughts
- Finished shelf, with a two-sentence takeaway written in your own words
- Reference shelf for essays, papers, and long articles worth returning to`

const longConfig = `## Scope

- Works inside \`Notes/Reading\` and \`Notes/Books\`
- Reads your saved reading lists and book notes when you ask for recommendations
- Keeps a simple index of finished books by year and theme

## Boundaries

- Never purchases books, subscribes to services, or sends external messages
- Never edits notes outside the reading folders without asking first
- Keeps all drafts and recommendations local to your vault

## Check-ins

- Quiet by default; replies when you message or @mention Alder`

const makeDraft = (rev, about, note) => ({
  draft: 'd-88776655',
  id: 'alder',
  name: 'Alder',
  rev,
  ready: true,
  problems: [],
  about,
  soul: longSoul(note),
  config: longConfig,
})

const at = (minute) => new Date(Date.UTC(2026, 10, 25, 18, minute)).toISOString()
const msg = (n, author, text, extra = {}) => ({
  id: `m${n}`,
  kind: author === 'system' ? 'system' : 'message',
  author,
  text,
  at: at(n),
  ...(author === maker.id ? { engine: 'claude-code' } : {}),
  ...extra,
})

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  res.end(JSON.stringify(body))
}
const sse = (res, event) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(`: open ping=15000\n\ndata: ${JSON.stringify(event)}\n\n`)
}

async function settle(page) {
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
  })
}

async function visibleMetrics(page, selector) {
  return page.evaluate((sel) => {
    const stream = document.querySelector('.tt-stream')
    const dock = document.querySelector('.tt-conversation .thread-dock')
    const el = document.querySelector(sel)
    if (!stream || !el) return null
    const sr = stream.getBoundingClientRect()
    const dr = dock ? dock.getBoundingClientRect() : { top: sr.bottom, bottom: sr.bottom, height: 0 }
    const er = el.getBoundingClientRect()
    const visibleTop = sr.top
    const visibleBottom = Math.min(sr.bottom, dr.top)
    return {
      top: er.top,
      bottom: er.bottom,
      height: er.height,
      visibleTop,
      visibleBottom,
      inView: er.top >= visibleTop - 2 && er.bottom <= visibleBottom + 2,
      intersects: er.bottom > visibleTop + 8 && er.top < visibleBottom - 8,
      aboveTop: er.bottom <= visibleTop + 2,
      streamScrollTop: stream.scrollTop,
      streamScrollHeight: stream.scrollHeight,
      streamClientHeight: stream.clientHeight,
      dockTop: dr.top,
      dockHeight: dr.height,
    }
  }, selector)
}

async function walk(width, theme) {
  const height = width === 390 ? 844 : 900
  const app = await oneProductFixture({}, before ? { publicDir: before } : {})
  const context = await browser.newContext({
    viewport: { width, height },
    colorScheme: theme,
    isMobile: width === 390,
    hasTouch: width === 390,
    reducedMotion: 'reduce',
  })

  try {
    let agent = null
    let currentDraft = makeDraft(1, 'A calm librarian who keeps your reading lists and book notes tidy.', 'Focuses on fiction, history, and essays you want to revisit.')
    let seq = 6
    const draftStreams = new Set()
    const ordinaryStreams = new Set()
    const deletedDrafts = []

    const draftMessages = [
      msg(1, 'you', 'Hey Scout! I was thinking about making a Librarian agent to help track books and reading notes.'),
      msg(2, maker.id, 'A librarian agent sounds lovely. What kind of reading do you want them to focus on?'),
      msg(3, 'you', 'Mostly history, essays, and literary fiction — and keeping a small next-up stack.'),
      msg(4, maker.id, 'Got it. I can sketch a calm archivist who keeps your reading lists and takeaways tidy without nagging.'),
      msg(5, maker.id, 'Here is a first draft for Alder below. Read through the soul and config, tell me what to adjust, or create Alder whenever it feels right.'),
      msg(6, 'system', 'Scout drafted a new agent: Alder (draft d-88776655, revision 1)'),
    ]

    const ordinaryMessages = Array.from({ length: 16 }, (_, i) =>
      msg(i + 1, i % 2 === 0 ? 'you' : maker.id,
        i % 2 === 0
          ? `Garden check-in #${i + 1}: the basil and thyme by the sunny window are growing fast this week.`
          : `Reply #${i + 1}: pinch back the top stems on the basil so it bushes out, and check the soil moisture tomorrow morning.`),
    )
    let ordinarySeq = ordinaryMessages.length

    const roster = () => (agent ? [maker, agent] : [maker])
    const r = app.routes
    r['GET /v1/agents'] = (_, res) => json(res, { agents: roster() })
    r['GET /v1/threads'] = (_, res) => json(res, { threads: [draftThread, ordinaryThread], agents: roster() })
    r['GET /v1/threads/events'] = (_, res) => sse(res, { type: 'threads.snapshot', threads: [draftThread, ordinaryThread], agents: roster() })

    r[`GET /v1/threads/${draftThread.id}`] = (_, res) => json(res, { thread: draftThread, messages: draftMessages, has_more: false, turn: null, turns: [] })
    r[`PATCH /v1/threads/${draftThread.id}`] = (_, res) => json(res, draftThread)
    r[`GET /v1/threads/${draftThread.id}/events`] = (_, res) => {
      draftStreams.add(res)
      res.on('close', () => draftStreams.delete(res))
      sse(res, { type: 'thread.snapshot', members: draftThread.members, turns: [] })
    }
    r[`POST /v1/threads/${draftThread.id}/messages`] = (_, res, _url, raw) => {
      const body = JSON.parse(raw || '{}')
      const m = msg(++seq, 'you', body.text)
      draftMessages.push(m)
      json(res, { accepted: true, message: m }, 201)
      for (const s of draftStreams) s.write(`data: ${JSON.stringify({ type: 'message.done', message: m })}\n\n`)
    }

    r[`GET /v1/threads/${ordinaryThread.id}`] = (_, res) => json(res, { thread: ordinaryThread, messages: ordinaryMessages, has_more: false, turn: null, turns: [] })
    r[`PATCH /v1/threads/${ordinaryThread.id}`] = (_, res) => json(res, ordinaryThread)
    r[`GET /v1/threads/${ordinaryThread.id}/events`] = (_, res) => {
      ordinaryStreams.add(res)
      res.on('close', () => ordinaryStreams.delete(res))
      sse(res, { type: 'thread.snapshot', members: ordinaryThread.members, turns: [] })
    }
    r[`POST /v1/threads/${ordinaryThread.id}/messages`] = (_, res, _url, raw) => {
      const body = JSON.parse(raw || '{}')
      const m = msg(++ordinarySeq, 'you', body.text)
      ordinaryMessages.push(m)
      json(res, { accepted: true, message: m }, 201)
      for (const s of ordinaryStreams) s.write(`data: ${JSON.stringify({ type: 'message.done', message: m })}\n\n`)
    }

    r['GET /v1/agents/drafts'] = (_, res, url) => json(res, {
      drafts: url.searchParams.get('thread') === draftThread.id && currentDraft ? [currentDraft] : [],
    })
    r[`DELETE /v1/agents/drafts/${currentDraft.draft}`] = (_, res) => {
      deletedDrafts.push(currentDraft?.draft)
      json(res, { ok: true })
    }

    const pushDraftEvent = (ev) => {
      if (ev.type === 'message.done') draftMessages.push(ev.message)
      for (const s of draftStreams) s.write(`data: ${JSON.stringify(ev)}\n\n`)
    }
    const pushOrdinaryEvent = (ev) => {
      if (ev.type === 'message.done') ordinaryMessages.push(ev.message)
      for (const s of ordinaryStreams) s.write(`data: ${JSON.stringify(ev)}\n\n`)
    }

    const page = await context.newPage()
    page.setDefaultTimeout(12000)
    const errors = []
    page.on('pageerror', e => errors.push(e.message))

    const shot = async (name) => {
      await settle(page)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${name}: no horizontal overflow`)
      if (out) await page.screenshot({ path: path.join(out, `${name}-${width}-${theme}.png`), animations: 'disabled' })
    }

    const typeAndSend = async (text) => {
      const field = page.locator('.draft')
      await field.click()
      await field.fill(text)
      await page.locator('.roundsend').click()
    }

    // ---------------------------------------------------------------- Journey 1
    // Agent drafts -> card appears -> expand -> send while expanded -> sent message, thinking & reply stay visible.
    await page.goto(`${app.url}/threads/${draftThread.id}`)
    await page.locator('.agent-draft-card').waitFor()
    await until(() => draftStreams.size > 0)
    await settle(page)
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme)

    const dockBefore = await page.locator('.tt-conversation .thread-dock').boundingBox()
    const composerBefore = await page.locator('.tt-conversation .composer-box').boundingBox()
    await page.getByRole('button', { name: 'Read the whole thing' }).click()
    await page.locator('.agent-draft-full').waitFor()
    await settle(page)

    const dockExpanded = await page.locator('.tt-conversation .thread-dock').boundingBox()
    const composerExpanded = await page.locator('.tt-conversation .composer-box').boundingBox()
    assert.ok(Math.abs(dockExpanded.y - dockBefore.y) <= 1, 'dock does not jump on expand')
    assert.ok(Math.abs(composerExpanded.y - composerBefore.y) <= 1, 'composer does not jump on expand')

    if (!before) {
      // Expanding calmly brings the card's header into view instead of yanking past the soul.
      const cardHeader = await visibleMetrics(page, '.agent-draft-card .agent-draft-head')
      assert.ok(cardHeader.inView, `expanded card header should be in view: ${JSON.stringify(cardHeader)}`)

      // Collapsing manually via Show less keeps the dock and composer rock-solid.
      await page.getByRole('button', { name: 'Show less' }).click()
      await page.locator('.agent-draft-full').waitFor({ state: 'detached' })
      await settle(page)
      const dockManualCollapse = await page.locator('.tt-conversation .thread-dock').boundingBox()
      const composerManualCollapse = await page.locator('.tt-conversation .composer-box').boundingBox()
      assert.ok(Math.abs(dockManualCollapse.y - dockBefore.y) <= 1, 'dock does not jump on collapse')
      assert.ok(Math.abs(composerManualCollapse.y - composerBefore.y) <= 1, 'composer does not jump on collapse')

      // Re-expand for the send-while-expanded journey.
      await page.getByRole('button', { name: 'Read the whole thing' }).click()
      await page.locator('.agent-draft-full').waitFor()
      await settle(page)
    }

    // Send a message while the card is expanded.
    const sentId = `m${seq + 1}`
    await typeAndSend('Can you also add a short weekly reading recap?')
    await page.locator(`[data-message-id="${sentId}"]`).waitFor()
    await settle(page)

    if (before) {
      // Baseline proof: her newly sent message is pushed completely off-screen above the tall expanded card.
      const sentBefore = await visibleMetrics(page, `[data-message-id="${sentId}"]`)
      assert.ok(sentBefore.aboveTop, `expected baseline to push sent message off screen, got ${JSON.stringify(sentBefore)}`)
      await shot('before-send-expanded')

      // Push agent reply too so baseline shows both user message and reply off-screen.
      const replyId = `m${++seq}`
      pushDraftEvent({ type: 'message.done', message: msg(seq, maker.id, 'Sure — I updated Alder with a quiet Sunday reading recap and kept the rest the same.') })
      await page.locator(`[data-message-id="${replyId}"]`).waitFor()
      await settle(page)
      const replyBefore = await visibleMetrics(page, `[data-message-id="${replyId}"]`)
      assert.ok(replyBefore.aboveTop, `expected baseline to push reply off screen, got ${JSON.stringify(replyBefore)}`)
      await shot('before-reply-expanded')
      console.log(`${width}px ${theme}: baseline confirmed sent message and reply pushed above viewport by expanded card`)
      return
    }

    // After: card collapsed, composer steady, sent message clearly visible above the compact card.
    assert.equal(await page.locator('.agent-draft-card').getAttribute('data-expanded'), 'false')
    assert.equal(await page.locator('.agent-draft-full').count(), 0)
    const sentAfter = await visibleMetrics(page, `[data-message-id="${sentId}"]`)
    const cardAfterSend = await visibleMetrics(page, '.agent-draft-card')
    assert.ok(sentAfter.inView, `sent message must be fully visible: ${JSON.stringify(sentAfter)}`)
    assert.ok(cardAfterSend.inView, `collapsed draft card should sit cleanly below sent message: ${JSON.stringify(cardAfterSend)}`)
    assert.ok(sentAfter.bottom < cardAfterSend.top, 'sent message sits right above the compact card')
    const composerCollapsed = await page.locator('.tt-conversation .composer-box').boundingBox()
    assert.ok(Math.abs(composerCollapsed.y - composerBefore.y) <= 1, `composer does not jump on send/collapse (${composerCollapsed.y} vs ${composerBefore.y})`)
    assert.ok(Math.abs((composerCollapsed.y + composerCollapsed.height) - (composerBefore.y + composerBefore.height)) <= 1, 'composer bottom stays anchored on send/collapse')

    // Audit all card controls against design tokens and >=40px target size.
    assertControls(await auditControls(page, '.agent-draft-card button'))
    await shot('j1-sent-visible')

    // Agent starts thinking -> thinking indicator stays visible right above the compact card.
    pushDraftEvent({ type: 'turn.started', persona: maker.id, turn_id: 'turn-rev2' })
    await page.locator('.awaiting').waitFor()
    await settle(page)
    const thinkingMetrics = await visibleMetrics(page, '.awaiting')
    assert.ok(thinkingMetrics.inView, `agent thinking line must be visible: ${JSON.stringify(thinkingMetrics)}`)
    await shot('j1-thinking-visible')

    // Agent sends reply -> both sent message and reply stay visible above the compact card.
    const replyId = `m${++seq}`
    pushDraftEvent({
      type: 'message.done',
      message: msg(seq, maker.id, 'Done — I added a quiet Sunday reading recap to Alder’s soul and kept the three-book next-up stack.'),
    })
    await page.locator(`[data-message-id="${replyId}"]`).waitFor()
    await settle(page)
    const replyAfter = await visibleMetrics(page, `[data-message-id="${replyId}"]`)
    const sentWithReply = await visibleMetrics(page, `[data-message-id="${sentId}"]`)
    assert.ok(replyAfter.inView, `agent reply must be visible: ${JSON.stringify(replyAfter)}`)
    assert.ok(sentWithReply.inView, `sent message remains visible beside reply: ${JSON.stringify(sentWithReply)}`)
    await shot('j1-reply-visible')

    // Short viewport check (simulates phone software keyboard reducing visible stream height).
    if (width === 390) {
      await page.setViewportSize({ width: 390, height: 430 })
      await settle(page)
      const sentShort = `m${seq + 1}`
      await typeAndSend('Looks great so far.')
      await page.locator(`[data-message-id="${sentShort}"]`).waitFor()
      await settle(page)
      const shortMetrics = await visibleMetrics(page, `[data-message-id="${sentShort}"]`)
      assert.ok(shortMetrics.inView, `sent message must stay visible even on keyboard-height viewport: ${JSON.stringify(shortMetrics)}`)
      await page.setViewportSize({ width, height })
      await settle(page)
    }

    // ---------------------------------------------------------------- Journey 2
    // Revision arrives -> card updates in place and collapses -> user expands with one tap.
    await page.getByRole('button', { name: 'Read the whole thing' }).click()
    await page.locator('.agent-draft-full').waitFor()
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').getAttribute('data-expanded'), 'true')

    currentDraft = makeDraft(
      2,
      'A calm librarian who keeps your reading lists tidy and sends a gentle Sunday recap.',
      'Includes a brief Sunday evening recap of what you read and one quiet suggestion.',
    )
    const rev2SysId = `m${++seq}`
    pushDraftEvent({ type: 'message.done', message: msg(seq, 'system', 'Scout revised draft d-88776655: Alder, revision 2 (soul)') })
    pushDraftEvent({ type: 'turn.finished', persona: maker.id, turn_id: 'turn-rev2' })
    await page.locator('.agent-draft-rev', { hasText: '2nd draft' }).waitFor()
    await settle(page)

    assert.equal(await page.locator('.agent-draft-card').getAttribute('data-expanded'), 'false', 'new revision collapses expanded card')
    assert.equal(await page.locator('.agent-draft-full').count(), 0)
    const rev2Sys = await visibleMetrics(page, `[data-message-id="${rev2SysId}"]`)
    const rev2Card = await visibleMetrics(page, '.agent-draft-card')
    assert.ok(rev2Sys.inView && rev2Card.inView, 'revision system line and updated compact card are both visible')
    await shot('j2-revision-collapsed')

    // One tap expands rev 2 cleanly.
    await page.getByRole('button', { name: 'Read the whole thing' }).click()
    await page.locator('.agent-draft-full').waitFor()
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').getAttribute('data-expanded'), 'true')
    const rev2Header = await visibleMetrics(page, '.agent-draft-card .agent-draft-head')
    assert.ok(rev2Header.inView, 'expanded 2nd draft header sits calmly in view')
    await shot('j2-revision-expanded')

    // ---------------------------------------------------------------- Journey 3
    // Close card -> hidden without deleting draft -> persists across reload -> rev 3 brings it back collapsed.
    await page.getByRole('button', { name: 'Hide Alder draft' }).click()
    await page.locator('.agent-draft-card').waitFor({ state: 'detached' })
    await settle(page)
    assert.deepEqual(deletedDrafts, [], 'closing never deletes the draft on the broker')
    const sysAfterClose = await visibleMetrics(page, `[data-message-id="${rev2SysId}"]`)
    assert.ok(sysAfterClose.inView, 'latest message settles cleanly above composer when card is closed')
    await shot('j3-card-closed')

    // Reload: rev 2 remains hidden.
    await page.reload()
    await page.locator(`[data-message-id="${rev2SysId}"]`).waitFor()
    await until(() => draftStreams.size > 0)
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').count(), 0, 'closed draft stays hidden after reload')

    // Later revision (rev 3) arrives -> card returns, collapsed!
    currentDraft = makeDraft(
      3,
      'A calm librarian for your reading lists, Sunday recaps, and book-club notes.',
      'Also keeps track of shared book-club discussion questions.',
    )
    pushDraftEvent({ type: 'message.done', message: msg(++seq, 'system', 'Scout revised draft d-88776655: Alder, revision 3 (soul, config)') })
    pushDraftEvent({ type: 'turn.finished', persona: maker.id, turn_id: 'turn-rev3' })
    await page.locator('.agent-draft-rev', { hasText: '3rd draft' }).waitFor()
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').getAttribute('data-expanded'), 'false', 'returned card is collapsed')
    const rev3Card = await visibleMetrics(page, '.agent-draft-card')
    assert.ok(rev3Card.inView, 'returned 3rd draft card is visible')
    await shot('j3-revision-returns')

    // ---------------------------------------------------------------- Journey 4
    // Close card again, then make Alder through chat -> draft ends and Agent created card appears.
    await page.getByRole('button', { name: 'Hide Alder draft' }).click()
    await page.locator('.agent-draft-card').waitFor({ state: 'detached' })
    await settle(page)

    const yesId = `m${seq + 1}`
    await typeAndSend('Yes, please make Alder now.')
    await page.locator(`[data-message-id="${yesId}"]`).waitFor()
    currentDraft = null
    agent = { id: 'alder', name: 'Alder', face: null }
    const madeId = `m${++seq}`
    pushDraftEvent({ type: 'message.done', message: msg(seq, 'system', 'Made Alder (alder): their first thread is t-f0e1d2c3') })
    pushDraftEvent({ type: 'turn.finished', persona: maker.id, turn_id: 'turn-make' })
    await page.locator('.tt-agent-created').waitFor()
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').count(), 0, 'draft card is gone once made')
    const createdMetrics = await visibleMetrics(page, `[data-message-id="${madeId}"]`)
    assert.ok(createdMetrics.inView, 'Agent created card is visible')
    await shot('j4-agent-made')

    // ---------------------------------------------------------------- Journey 5
    // Ordinary thread with no draft card scrolls identically to before.
    await page.goto(`${app.url}/threads/${ordinaryThread.id}`)
    await page.locator(`[data-message-id="m${ordinarySeq}"]`).waitFor()
    await until(() => ordinaryStreams.size > 0)
    await settle(page)
    assert.equal(await page.locator('.agent-draft-card').count(), 0)

    const ordBottom = await page.evaluate(() => {
      const s = document.querySelector('.tt-stream')
      return s.scrollHeight - s.scrollTop - s.clientHeight
    })
    assert.ok(ordBottom <= 2, `ordinary thread opens pinned at end (gap=${ordBottom})`)

    // Scroll up into history -> incoming message does not yank the reader down.
    const scrolledPos = await page.evaluate(() => {
      const s = document.querySelector('.tt-stream')
      s.scrollTop = Math.max(0, s.scrollTop - 320)
      return s.scrollTop
    })
    await settle(page)
    pushOrdinaryEvent({ type: 'message.done', message: msg(++ordinarySeq, maker.id, 'Quick follow-up: the mint also likes a bit of afternoon shade.') })
    await page.locator(`[data-message-id="m${ordinarySeq}"]`).waitFor()
    await settle(page)
    const afterPassive = await page.evaluate(() => document.querySelector('.tt-stream').scrollTop)
    assert.ok(Math.abs(afterPassive - scrolledPos) <= 2, `ordinary thread stays put when scrolled up (${afterPassive} vs ${scrolledPos})`)

    // Sending a message lands back at the end cleanly.
    const ordSentId = `m${ordinarySeq + 1}`
    await typeAndSend('Thanks, I will move the mint pot this afternoon.')
    await page.locator(`[data-message-id="${ordSentId}"]`).waitFor()
    await settle(page)
    const ordEndGap = await page.evaluate(() => {
      const s = document.querySelector('.tt-stream')
      return s.scrollHeight - s.scrollTop - s.clientHeight
    })
    assert.ok(ordEndGap <= 2, `ordinary thread send lands at bottom (gap=${ordEndGap})`)
    const ordSentMetrics = await visibleMetrics(page, `[data-message-id="${ordSentId}"]`)
    assert.ok(ordSentMetrics.inView, 'ordinary thread sent message is in view')
    await shot('j5-ordinary-thread')

    assert.deepEqual(errors, [], `unexpected page errors: ${errors.join('; ')}`)
    console.log(`${width}px ${theme}: all 5 journeys + control audit + keyboard viewport passed`)
  } finally {
    await context.close()
    await app.close()
  }
}

try {
  for (const width of [390, 1440]) {
    for (const theme of ['light', 'dark']) {
      await walk(width, theme)
    }
  }
} finally {
  await browser.close()
}
