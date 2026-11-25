// Real Chromium verification that open question and approval cards stay where
// they were raised while newly sent messages and streaming replies stay in view
// at the bottom, across phone (390px) and desktop (1440px), light and dark:
//   1. Open AskUserQuestion card (no message_id) -> send message -> sent message
//      and streaming reply stay visible at the bottom -> scroll up & answer card.
//   2. Live Bash/Write approval cards (with intro prose before send) -> send
//      message -> card stays anchored above the sent message -> Allow & Deny work.
//   3. Ordinary thread with no permission cards scrolls identically to before.
//
// Run (from app/):
//   LOAM_CHROME=/usr/bin/chromium-headless-shell node test/permission-card-scroll-browser.mjs
// Optional:
//   PERMISSION_EVIDENCE=<dir>         save PNG frames
//   PERMISSION_BEFORE_PUBLIC=<dir>    run baseline check against previous public/
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { until } from './harness.js'
import { oneProductFixture } from './one-product-fixture.mjs'

const CHROME = process.env.LOAM_CHROME || process.env.APP_PREVIEW_CHROME || '/usr/bin/chromium-headless-shell'
const before = process.env.PERMISSION_BEFORE_PUBLIC || ''
const out = process.env.PERMISSION_EVIDENCE || ''
if (out) mkdirSync(out, { recursive: true })

const scout = { id: 'scout', name: 'Scout' }
const quill = { id: 'quill', name: 'Quill' }
const questionThread = {
  id: 't-q1a2b3c4',
  title: 'Weekly review setup',
  default: scout.id,
  members: [
    { persona: scout.id, engine: 'claude-code' },
    { persona: quill.id, engine: 'claude-code' },
  ],
}
const ordinaryThread = {
  id: 't-0rd1n4r7',
  title: 'Weekend garden notes',
  default: scout.id,
  members: [{ persona: scout.id, engine: 'claude-code' }],
}

const at = (minute) => new Date(Date.UTC(2026, 10, 25, 18, minute)).toISOString()
const msg = (n, author, text, extra = {}) => ({
  id: `m${n}`,
  kind: author === 'system' ? 'system' : 'message',
  author,
  text,
  at: at(n),
  ...(author !== 'you' && author !== 'system' ? { engine: 'claude-code' } : {}),
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

async function launchCdp({ width, height, theme }) {
  const dir = mkdtempSync(path.join(tmpdir(), 'loam-perm-cdp-'))
  const proc = spawn(
    CHROME,
    [
      '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
      '--hide-scrollbars', '--remote-debugging-port=0',
      `--user-data-dir=${path.join(dir, 'profile')}`,
      `--window-size=${width},${height}`, 'about:blank',
    ],
    { stdio: 'ignore' },
  )
  const port = await new Promise((resolve, reject) => {
    const start = Date.now()
    const poll = () => {
      const f = path.join(dir, 'profile', 'DevToolsActivePort')
      if (existsSync(f)) {
        const p = Number(readFileSync(f, 'utf8').split(/\r?\n/)[0])
        if (p) return resolve(p)
      }
      if (Date.now() - start > 10000) return reject(new Error('Chromium DevTools port timeout'))
      setTimeout(poll, 30)
    }
    poll()
  })
  const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json()
  const pageTarget = targets.find((t) => t.type === 'page')
  const ws = new WebSocket(pageTarget.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let seq = 0
  const pending = new Map()
  const errors = []
  ws.onmessage = ({ data }) => {
    const payload = JSON.parse(data)
    if (payload.method === 'Runtime.exceptionThrown') {
      errors.push(payload.params?.exceptionDetails?.exception?.description || payload.params?.exceptionDetails?.text || 'pageerror')
    }
    if (payload.id && pending.has(payload.id)) {
      const { resolve, reject } = pending.get(payload.id)
      pending.delete(payload.id)
      if (payload.error) reject(new Error(payload.error.message))
      else resolve(payload.result)
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', {
    width, height, deviceScaleFactor: 2, mobile: width <= 480,
  })
  await send('Emulation.setEmulatedMedia', {
    features: [
      { name: 'prefers-color-scheme', value: theme },
      { name: 'prefers-reduced-motion', value: 'reduce' },
    ],
  })
  const evalJs = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text)
    return r.result?.value
  }
  const waitFor = async (expression, ms = 8000) => {
    const start = Date.now()
    while (Date.now() - start < ms) {
      if (await evalJs(expression)) return
      await new Promise((r) => setTimeout(r, 40))
    }
    throw new Error(`Timeout waiting for: ${expression}`)
  }
  const settle = () => evalJs(`new Promise(r => document.fonts.ready.then(() => requestAnimationFrame(() => requestAnimationFrame(r))))`)
  const typeAndSend = async (text) => {
    await waitFor(`Boolean(document.querySelector('.draft'))`)
    await evalJs(`(() => { const f = document.querySelector('.draft'); f.focus(); f.textContent = ''; })()`)
    await send('Input.insertText', { text })
    await settle()
    await evalJs(`document.querySelector('.roundsend').click()`)
  }
  const shot = async (name) => {
    await settle()
    const overflow = await evalJs(`document.documentElement.scrollWidth > innerWidth`)
    assert.equal(overflow, false, `${name} (${width}px ${theme}): no horizontal overflow`)
    if (out) {
      const { data } = await send('Page.captureScreenshot', { format: 'png' })
      writeFileSync(path.join(out, `${name}-${width}-${theme}.png`), Buffer.from(data, 'base64'))
    }
  }
  const close = () => {
    try { ws.close() } catch {}
    proc.kill()
    rmSync(dir, { recursive: true, force: true })
  }
  return { send, evalJs, waitFor, settle, typeAndSend, shot, errors, close }
}

async function measureAll(cdp, selectors) {
  return cdp.evalJs(`(() => {
    const stream = document.querySelector('.tt-stream')
    const dock = document.querySelector('.tt-conversation .thread-dock')
    if (!stream) return null
    const sr = stream.getBoundingClientRect()
    const dr = dock ? dock.getBoundingClientRect() : { top: sr.bottom }
    const visibleTop = sr.top
    const visibleBottom = Math.min(sr.bottom, dr.top)
    const out = {}
    for (const [key, sel] of Object.entries(${JSON.stringify(selectors)})) {
      const el = document.querySelector(sel)
      if (!el) { out[key] = null; continue }
      const er = el.getBoundingClientRect()
      out[key] = {
        top: er.top,
        bottom: er.bottom,
        height: er.height,
        visibleTop,
        visibleBottom,
        inView: er.top >= visibleTop - 2 && er.bottom <= visibleBottom + 2,
        aboveTop: er.bottom <= visibleTop + 2,
        scrollTop: stream.scrollTop,
        scrollHeight: stream.scrollHeight,
        clientHeight: stream.clientHeight,
        atEnd: stream.scrollHeight - stream.scrollTop - stream.clientHeight <= 2,
      }
    }
    return out
  })()`)
}

async function metrics(cdp, selector) {
  return (await measureAll(cdp, { el: selector }))?.el ?? null
}

async function walk(width, theme) {
  const height = width === 390 ? 844 : 900
  const app = await oneProductFixture({}, before ? { publicDir: before } : {})
  const cdp = await launchCdp({ width, height, theme })

  try {
    let seq = 6
    const qStreams = new Set()
    const oStreams = new Set()
    const answered = []

    const questionReq = {
      type: 'broker.permission_request',
      kind: 'question',
      request_id: 'req-weekly-1',
      session_id: 's-scout-1',
      persona: scout.id,
      tool_name: 'AskUserQuestion',
      message: 'Choose how the weekly review should gather and format notes.',
      questions: [
        {
          id: 'scope',
          header: 'Scope',
          question: 'Which folders should the Friday review scan?',
          options: [
            { label: 'Active projects only', description: 'Skip reference shelves and archived folders.' },
            { label: 'Active projects + reading notes', description: 'Include highlights added during the week.' },
            { label: 'Everything modified this week', description: 'Sweep the whole vault by modification date.' },
          ],
          other: true,
        },
        {
          id: 'format',
          header: 'Summary format',
          question: 'How would you like the review note structured?',
          options: [
            { label: 'Short bullet list', description: 'Three sections: finished, waiting, next week.' },
            { label: 'Table with owners and dates', description: 'Compact overview grouped by project.' },
          ],
          other: false,
        },
      ],
    }

    let pendingPermissions = [questionReq]
    const qMessages = [
      msg(1, 'you', 'Can you help set up a light weekly review routine for my project notes?'),
      msg(2, scout.id, 'Glad to. I read through your active folders and there are three clusters: client work, reading notes, and home admin.'),
      msg(3, 'you', 'Keep it brief so I actually finish it on Friday afternoons.'),
      msg(4, scout.id, 'Makes sense. A ten-minute check-in works best when we decide up front which folders to scan and how to format the summary.'),
      msg(5, 'you', 'Go ahead and propose the choices and ask me what you need.'),
      msg(6, scout.id, 'Before I write the template, I have two quick questions below.'),
    ]
    const oMessages = Array.from({ length: 16 }, (_, i) =>
      msg(i + 1, i % 2 === 0 ? 'you' : scout.id,
        i % 2 === 0
          ? `Garden check-in #${i + 1}: the basil and thyme by the sunny window are growing fast this week.`
          : `Reply #${i + 1}: pinch back the top stems on the basil so it bushes out, and check the soil moisture tomorrow morning.`),
    )
    let oSeq = oMessages.length

    const r = app.routes
    r['GET /v1/agents'] = (_, res) => json(res, { agents: [scout, quill] })
    r['GET /v1/threads'] = (_, res) => json(res, { threads: [questionThread, ordinaryThread], agents: [scout, quill] })
    r['GET /v1/threads/events'] = (_, res) => sse(res, { type: 'threads.snapshot', threads: [questionThread, ordinaryThread], agents: [scout, quill] })

    r[`GET /v1/threads/${questionThread.id}`] = (_, res) => json(res, {
      thread: questionThread, messages: qMessages, has_more: false,
      turn: { persona: scout.id, engine: 'claude-code' },
      turns: [{ persona: scout.id, engine: 'claude-code' }],
      pending_permissions: pendingPermissions,
    })
    r[`PATCH /v1/threads/${questionThread.id}`] = (_, res) => json(res, questionThread)
    r[`GET /v1/threads/${questionThread.id}/events`] = (_, res) => {
      qStreams.add(res)
      res.on('close', () => qStreams.delete(res))
      sse(res, {
        type: 'thread.snapshot', members: questionThread.members,
        turns: [{ persona: scout.id, engine: 'claude-code' }],
        pending: pendingPermissions,
      })
    }
    r[`POST /v1/threads/${questionThread.id}/messages`] = (_, res, _url, raw) => {
      const body = JSON.parse(raw || '{}')
      const m = msg(++seq, 'you', body.text)
      qMessages.push(m)
      json(res, { accepted: true, message: m }, 201)
      for (const s of qStreams) s.write(`data: ${JSON.stringify({ type: 'message.done', message: m })}\n\n`)
    }
    for (const reqId of ['req-weekly-1', 'req-bash-allow', 'req-write-deny']) {
      r[`POST /v1/sessions/s-scout-1/permissions/${reqId}`] = (_, res, _url, raw) => {
        const body = JSON.parse(raw || '{}')
        answered.push({ request_id: reqId, body })
        pendingPermissions = pendingPermissions.filter((p) => p.request_id !== reqId)
        json(res, { ok: true })
        for (const s of qStreams) s.write(`data: ${JSON.stringify({ type: 'broker.permission_resolved', request_id: reqId })}\n\n`)
      }
    }

    r[`GET /v1/threads/${ordinaryThread.id}`] = (_, res) => json(res, {
      thread: ordinaryThread, messages: oMessages, has_more: false, turn: null, turns: [],
    })
    r[`PATCH /v1/threads/${ordinaryThread.id}`] = (_, res) => json(res, ordinaryThread)
    r[`GET /v1/threads/${ordinaryThread.id}/events`] = (_, res) => {
      oStreams.add(res)
      res.on('close', () => oStreams.delete(res))
      sse(res, { type: 'thread.snapshot', members: ordinaryThread.members, turns: [] })
    }
    r[`POST /v1/threads/${ordinaryThread.id}/messages`] = (_, res, _url, raw) => {
      const body = JSON.parse(raw || '{}')
      const m = msg(++oSeq, 'you', body.text)
      oMessages.push(m)
      json(res, { accepted: true, message: m }, 201)
      for (const s of oStreams) s.write(`data: ${JSON.stringify({ type: 'message.done', message: m })}\n\n`)
    }

    const pushQ = (ev) => {
      if (ev.type === 'message.done') qMessages.push(ev.message)
      if (ev.type === 'broker.permission_request') pendingPermissions.push(ev)
      for (const s of qStreams) s.write(`data: ${JSON.stringify(ev)}\n\n`)
    }
    const pushO = (ev) => {
      if (ev.type === 'message.done') oMessages.push(ev.message)
      for (const s of oStreams) s.write(`data: ${JSON.stringify(ev)}\n\n`)
    }

    // -------------------------------------------------------------- Journey 1
    await cdp.send('Page.navigate', { url: `${app.url}/threads/${questionThread.id}` })
    await cdp.waitFor(`Boolean(document.querySelector('.approve.tt-approval .question'))`)
    await until(() => qStreams.size > 0)
    await cdp.settle()
    assert.equal(await cdp.evalJs(`document.documentElement.getAttribute('data-theme')`), theme)

    if (!before) {
      // On open, the question card's Send / Decline controls are immediately visible at the bottom.
      const buttons = await metrics(cdp, '.approve.tt-approval .buttons')
      assert.ok(buttons.inView, `question card actions should be in view on open: ${JSON.stringify(buttons)}`)
      await cdp.shot('j1-card-open')
    }

    // Send a message while the question card is open.
    await cdp.typeAndSend('@quill Could you check if Friday afternoon is clear before I answer?')
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m7"]'))`)
    await cdp.settle()

    // Quill streams a reply.
    pushQ({ type: 'turn.started', persona: quill.id, engine: 'claude-code' })
    pushQ({ type: 'message.delta', persona: quill.id, draft: 'Friday afternoon is open from 2pm to 4pm, so a ten-minute review fits easily.' })
    await cdp.waitFor(`document.body.textContent.includes('Friday afternoon is open from 2pm')`)
    await cdp.settle()

    if (before) {
      const sentBefore = await metrics(cdp, '[data-message-id="m7"]')
      assert.ok(sentBefore.aboveTop, `expected baseline to push sent message off screen at ${width}px ${theme}, got ${JSON.stringify(sentBefore)}`)
      await cdp.shot('before-send-reply')
      console.log(`${width}px ${theme}: baseline confirmed sent message pushed above viewport (bottom=${Math.round(sentBefore.bottom)}, visTop=${Math.round(sentBefore.visibleTop)})`)
      return
    }

    // After fix: sent message and streaming reply are fully in view at the bottom, below the anchored card.
    const { sent: sentAfter, reply: streamReply, card: cardAfter } = await measureAll(cdp, {
      sent: '[data-message-id="m7"]',
      reply: '[data-message-id="draft:quill"]',
      card: '.approve.tt-approval',
    })
    assert.ok(sentAfter.inView, `sent message must be in view: ${JSON.stringify(sentAfter)}`)
    assert.ok(streamReply.inView, `streaming reply must be in view: ${JSON.stringify(streamReply)}`)
    assert.ok(cardAfter.bottom <= sentAfter.top + 2, `question card stays above sent message (${cardAfter.bottom} <= ${sentAfter.top})`)
    assert.ok(sentAfter.bottom <= streamReply.top + 2, `streaming reply sits below sent message`)
    const ghostScout = await cdp.evalJs(`document.querySelectorAll('[data-message-id="draft:scout"]').length`)
    assert.equal(ghostScout, 0, 'no empty ghost Scout header between sent message and Quill reply')
    await cdp.shot('j1-sent-reply-visible')

    // Quill finishes replying -> still in view at the bottom.
    const quillDone = msg(++seq, quill.id, 'Friday afternoon is open from 2pm to 4pm, so a ten-minute review fits easily.')
    pushQ({ type: 'message.done', message: quillDone })
    pushQ({ type: 'turn.finished', persona: quill.id })
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m8"]'))`)
    await cdp.settle()
    const doneReply = await metrics(cdp, '[data-message-id="m8"]')
    assert.ok(doneReply.inView, `finished reply must stay in view: ${JSON.stringify(doneReply)}`)

    // Scroll back up to the anchored question card, answer both questions, and send.
    await cdp.evalJs(`(() => {
      const card = document.querySelector('.approve.tt-approval')
      card.scrollIntoView({ block: 'center' })
      const pick = (label) => [...card.querySelectorAll('.qpick')].find((b) => b.textContent.includes(label))
      pick('Active projects + reading notes').click()
      pick('Short bullet list').click()
    })()`)
    await cdp.settle()
    await cdp.evalJs(`document.querySelector('.approve.tt-approval .allow').click()`)
    await cdp.waitFor(`!document.querySelector('.approve.tt-approval')`)
    assert.equal(answered.length, 1)
    assert.equal(answered[0].request_id, 'req-weekly-1')
    assert.deepEqual(answered[0].body, {
      behavior: 'allow',
      answers: { scope: ['Active projects + reading notes'], format: ['Short bullet list'] },
    })

    // Scout finishes its turn after getting the answer.
    const scoutDone = msg(++seq, scout.id, 'Template saved — active projects plus reading notes, formatted as a short bullet list for Friday at 2pm.')
    pushQ({ type: 'message.done', message: scoutDone })
    pushQ({ type: 'turn.finished', persona: scout.id })
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m9"]'))`)
    await cdp.evalJs(`(() => { const s = document.querySelector('.tt-stream'); s.scrollTop = s.scrollHeight; })()`)
    await cdp.settle()
    await cdp.shot('j1-answered-complete')

    // -------------------------------------------------------------- Journey 2
    // Live approval cards (Allow and Deny) with streaming intro prose before send.
    pushQ({ type: 'turn.started', persona: scout.id, engine: 'claude-code' })
    pushQ({ type: 'message.delta', persona: scout.id, draft: 'I can test the review script right now if you approve:' })
    pushQ({
      type: 'broker.permission_request',
      request_id: 'req-bash-allow',
      session_id: 's-scout-1',
      persona: scout.id,
      tool_name: 'Bash',
      input: { command: 'ls -la Notes/Projects' },
      decision_reason: 'Check active project folders for the template.',
    })
    await cdp.waitFor(`Boolean(document.querySelector('.approve.tt-approval .askrow'))`)
    await cdp.settle()

    // Before send, the approval card sits below Scout's streaming intro prose.
    const { intro: introBefore, card: bashCardBefore } = await measureAll(cdp, {
      intro: '[data-message-id="draft:scout"]',
      card: '.approve.tt-approval',
    })
    assert.ok(introBefore.bottom <= bashCardBefore.top + 2, `approval card sits below streaming intro prose before send (${introBefore.bottom} <= ${bashCardBefore.top})`)

    // Send a message while the approval card is open -> card anchors above sent message.
    await cdp.typeAndSend('Go ahead, and skip archived folders too.')
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m10"]'))`)
    await cdp.settle()
    const { sent: sentDuringBash, card: bashCardAfter } = await measureAll(cdp, {
      sent: '[data-message-id="m10"]',
      card: '.approve.tt-approval',
    })
    assert.ok(sentDuringBash.inView, `message sent during Bash approval must be in view: ${JSON.stringify(sentDuringBash)}`)
    assert.ok(bashCardAfter.bottom <= sentDuringBash.top + 2, `Bash approval card stays above sent message`)

    // Approve the card -> POST allow & card disappears.
    await cdp.evalJs(`document.querySelector('.approve.tt-approval .allow').click()`)
    await cdp.waitFor(`!document.querySelector('.approve.tt-approval')`)
    assert.equal(answered.at(-1).request_id, 'req-bash-allow')
    assert.deepEqual(answered.at(-1).body, { behavior: 'allow' })
    pushQ({ type: 'turn.finished', persona: scout.id })

    // Second approval card -> send a message -> Deny works.
    pushQ({
      type: 'broker.permission_request',
      request_id: 'req-write-deny',
      session_id: 's-scout-1',
      persona: scout.id,
      tool_name: 'Write',
      input: { file_path: 'Notes/Archive/index.md' },
      decision_reason: 'Update archive index.',
    })
    await cdp.waitFor(`Boolean(document.querySelector('.approve.tt-approval .askrow'))`)
    await cdp.typeAndSend('Wait, leave the archive folder untouched.')
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m11"]'))`)
    await cdp.settle()
    const { sent: sentDuringWrite, card: writeCardAfter } = await measureAll(cdp, {
      sent: '[data-message-id="m11"]',
      card: '.approve.tt-approval',
    })
    assert.ok(sentDuringWrite.inView, `message sent during Write approval must be in view`)
    assert.ok(writeCardAfter.bottom <= sentDuringWrite.top + 2, `Write approval card stays above sent message`)
    await cdp.evalJs(`document.querySelector('.approve.tt-approval .deny').click()`)
    await cdp.waitFor(`!document.querySelector('.approve.tt-approval')`)
    assert.equal(answered.at(-1).request_id, 'req-write-deny')
    assert.deepEqual(answered.at(-1).body, { behavior: 'deny' })

    // -------------------------------------------------------------- Journey 3
    // Ordinary thread with no permission cards behaves identically.
    await cdp.send('Page.navigate', { url: `${app.url}/threads/${ordinaryThread.id}` })
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m16"]'))`)
    await until(() => oStreams.size > 0)
    await new Promise((r) => setTimeout(r, 150))
    await cdp.settle()
    const ordEnd = await metrics(cdp, '[data-message-id="m16"]')
    assert.ok(ordEnd.inView && ordEnd.atEnd, `ordinary thread opens pinned at end: ${JSON.stringify(ordEnd)}`)

    // Scroll up into history with a real wheel gesture; an arriving reply does not yank the reader down.
    const beforeWheel = await cdp.evalJs(`(() => {
      const s = document.querySelector('.tt-stream')
      const r = s.getBoundingClientRect()
      return { top: s.scrollTop, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }
    })()`)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: beforeWheel.x, y: beforeWheel.y, deltaX: 0, deltaY: -360 })
    await cdp.waitFor(`document.querySelector('.tt-stream').scrollTop < ${beforeWheel.top - 100}`)
    await cdp.settle()
    const heldTop = await cdp.evalJs(`document.querySelector('.tt-stream').scrollTop`)
    pushO({ type: 'message.done', message: msg(++oSeq, scout.id, 'Water deeply tomorrow morning before the sun hits the leaves.') })
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m17"]'))`)
    await cdp.settle()
    const topAfterArrival = await cdp.evalJs(`document.querySelector('.tt-stream').scrollTop`)
    assert.ok(Math.abs(topAfterArrival - heldTop) <= 4, `scrolled-up ordinary thread stays put (${topAfterArrival} vs ${heldTop})`)

    // Sending from scrolled-up returns cleanly to the bottom.
    await cdp.typeAndSend('Will do, thanks Scout!')
    await cdp.waitFor(`Boolean(document.querySelector('[data-message-id="m18"]'))`)
    await cdp.settle()
    const ordSent = await metrics(cdp, '[data-message-id="m18"]')
    assert.ok(ordSent.inView && ordSent.atEnd, `sending in ordinary thread returns to bottom: ${JSON.stringify(ordSent)}`)
    await cdp.shot('j3-ordinary')

    assert.deepEqual(cdp.errors, [], `zero page errors at ${width}px ${theme}`)
    console.log(`${width}px ${theme}: all 3 journeys verified (question card + send/stream/answer, Bash allow + Write deny, ordinary thread unchanged)`)
  } finally {
    cdp.close()
    await app.close()
  }
}

for (const width of [390, 1440]) {
  for (const theme of ['light', 'dark']) {
    await walk(width, theme)
  }
}
