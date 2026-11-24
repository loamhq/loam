// Browser walk and before/after captures for open-thread live recovery:
//   - screen lock / background -> foreground (visibilitychange)
//   - silent socket stall (heartbeat timeout)
//   - network drop / server down (calm honest notice + Try again / online)
//   - bfcache restore (pageshow persisted)
// at 390x844 (phone) and 1440x900 (desktop) in light and dark modes.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   THREAD_LIVE_RECOVERY_EVIDENCE=/tmp/out node test/thread-live-recovery-browser.mjs
//
// Pass THREAD_LIVE_RECOVERY_BASELINE=1 to run the same walk against HEAD (main)
// and capture the pre-fix silent-stall regression.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.THREAD_LIVE_RECOVERY_EVIDENCE
const baseline = process.env.THREAD_LIVE_RECOVERY_BASELINE === '1'
const baseRef = process.env.THREAD_LIVE_RECOVERY_BASE || 'HEAD'
if (out) await mkdir(out, { recursive: true })

const BASELINE_FILES = ['app.js', 'threads.js', 'threads.css', 'link.js', 'thread-record.js']
const baselineCache = baseline
  ? Object.fromEntries(BASELINE_FILES.map((file) => [file, execFileSync('git', ['show', `${baseRef}:app/public/${file}`], { encoding: 'utf8' })]))
  : null

function numberOf(id) {
  return Number(String(id ?? '').match(/(\d+)$/)?.[1] ?? 0)
}

async function waitFor(fn, message, timeout = 12000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await fn()) return
    await new Promise((r) => setTimeout(r, 25))
  }
  throw new Error(`Timed out waiting for: ${message}`)
}

const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
})

const results = []
try {
  for (const width of [390, 1440]) {
    const height = width === 390 ? 844 : 900
    for (const theme of ['light', 'dark']) {
      const app = await oneProductFixture()
      const id = 'a-quieter-week'
      const errors = []
      const nowIso = () => new Date().toISOString()

      const threadState = {
        members: [
          { persona: 'sprout', engine: 'claude-code', model: null, busy: false, working: false, background: 0 },
          { persona: 'quill', engine: 'codex', model: null, busy: false, working: false, background: 0 },
        ],
        messages: [
          { id: 'm1', author: 'you', kind: 'message', at: '2026-11-23T19:55:00Z', text: 'Let me know once the check finishes.' },
          { id: 'm2', author: 'sprout', kind: 'message', engine: 'claude-code', model: 'claude-sonnet-4-6', at: '2026-11-23T19:56:00Z', text: 'On it — I will watch the run and report back here.', did: [] },
        ],
        turns: [],
        nextId: 3,
        streamStatus: 200,
      }

      const rows = (await app.f.api('GET', '/threads')).threads
      const summary = () => ({
        ...rows.find((t) => t.id === id),
        title: 'Release check',
        default: 'sprout',
        members: structuredClone(threadState.members),
        working: threadState.members.filter((m) => m.working).length,
        background: 0,
        unread: 0,
        last: threadState.messages.at(-1),
      })
      const listing = () => rows.map((t) => (t.id === id ? summary() : t))
      const json = (res, value, status = 200) => {
        res.writeHead(status, { 'content-type': 'application/json' })
        res.end(JSON.stringify(value))
      }

      const threadStreams = []
      let threadReads = 0

      app.routes['GET /v1/threads'] = (_req, res) => json(res, { threads: listing(), agents: app.f.agents })
      app.routes['GET /v1/threads/events'] = (_req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write(': open ping=15000\n\n')
        res.write(`data: ${JSON.stringify({ type: 'threads.snapshot', threads: listing(), agents: app.f.agents })}\n\n`)
      }
      app.routes[`GET /v1/threads/${id}`] = (_req, res, url) => {
        if (!url.searchParams.has('seen')) threadReads++
        json(res, {
          thread: summary(),
          messages: structuredClone(threadState.messages),
          has_more: false,
          turn: threadState.turns[0] ?? null,
          turns: structuredClone(threadState.turns),
          pending_permissions: [],
        })
      }
      app.routes[`PATCH /v1/threads/${id}`] = (_req, res) => json(res, summary())
      app.routes[`POST /v1/threads/${id}/messages`] = (_req, res, _url, raw) => {
        const body = JSON.parse(raw || '{}')
        const msg = { id: `m${threadState.nextId++}`, author: 'you', kind: 'message', at: nowIso(), text: body.text }
        threadState.messages.push(msg)
        // Start a real agent turn on the server when the owner writes.
        threadState.turns = [{ persona: 'sprout', engine: 'claude-code', started_at: nowIso() }]
        threadState.members[0].working = true
        threadState.members[0].busy = true
        // Deliver only to non-black-holed streams, modelling a half-open mobile socket.
        for (const s of threadStreams) {
          if (!s.blackholed && !s.closed) {
            s.writeEvent({ type: 'message.done', message: msg })
            s.writeEvent({ type: 'turn.started', persona: 'sprout', engine: 'claude-code' })
            s.writeEvent({ type: 'members', members: structuredClone(threadState.members), working: 1, background: 0 })
          }
        }
        json(res, { accepted: true, message: msg })
      }
      app.routes[`GET /v1/threads/${id}/events`] = (_req, res, url) => {
        if (threadState.streamStatus !== 200) {
          return json(res, { error: 'The broker is temporarily unreachable.' }, threadState.streamStatus)
        }
        const after = url.searchParams.get('after') ?? 'm0'
        const afterNum = numberOf(after)
        const entry = {
          after,
          res,
          blackholed: false,
          closed: false,
          writeEvent(ev) {
            if (!this.closed && !res.writableEnded) res.write(`data: ${JSON.stringify(ev)}\n\n`)
          },
          drop() {
            this.closed = true
            if (!res.writableEnded) res.end()
          },
        }
        threadStreams.push(entry)
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write(': open ping=15000\n\n')
        entry.writeEvent({
          type: 'thread.snapshot',
          thread: id,
          turn: threadState.turns[0] ?? null,
          turns: structuredClone(threadState.turns),
          marks: [],
          pending: [],
          members: structuredClone(threadState.members),
        })
        for (const msg of threadState.messages) {
          if (numberOf(msg.id) > afterNum) entry.writeEvent({ type: 'message.done', message: structuredClone(msg) })
        }
        res.on('close', () => { entry.closed = true })
      }

      const context = await browser.newContext({
        viewport: { width, height },
        colorScheme: theme,
        isMobile: width === 390,
        hasTouch: width === 390,
        reducedMotion: 'reduce',
      })
      if (baseline) {
        for (const file of BASELINE_FILES) {
          await context.route(`**/${file}`, (route) => route.fulfill({
            contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript',
            body: baselineCache[file],
          }))
        }
      }

      const page = await context.newPage()
      page.setDefaultTimeout(12000)
      page.on('pageerror', (e) => errors.push(e.message))

      // Install a controllable visibilityState shim so we can trigger real
      // hidden -> visible transitions inside Chromium.
      await page.addInitScript(() => {
        let state = 'visible'
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => state !== 'visible' })
        globalThis.__setVisibility = (next) => {
          state = next
          document.dispatchEvent(new Event('visibilitychange'))
        }
      })

      const captures = []
      const shot = async (name) => {
        await page.evaluate(async () => {
          await document.fonts.ready
          await new Promise(requestAnimationFrame)
          await new Promise(requestAnimationFrame)
        })
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
        assert.equal(overflow, false, `${name} (${width} ${theme}): no horizontal overflow`)
        if (out) {
          const file = `${baseline ? 'before' : 'after'}-${name}-${width}-${theme}.png`
          await page.screenshot({ path: path.join(out, file), animations: 'disabled' })
          captures.push(file)
        }
      }

      try {
        await page.goto(app.url)
        await page.getByTitle('Release check', { exact: true }).click()
        await page.locator('.tt-message').filter({ hasText: 'On it — I will watch the run' }).waitFor()
        await waitFor(() => threadStreams.length === 1, 'initial thread SSE stream')
        const readsAfterOpen = threadReads

        // --- Scenario 1: Screen lock / background kills socket silently, then user returns and sends ---
        threadStreams[0].blackholed = true
        await page.evaluate(() => globalThis.__setVisibility('hidden'))
        await page.evaluate(() => globalThis.__setVisibility('visible'))

        if (baseline) {
          // Send a message right after returning to the stale open thread — reproduces the reported symptom.
          await page.locator('#draft').click()
          await page.locator('#draft').fill('Did the second check connect too?')
          await page.getByTitle('Send', { exact: true }).click()
          await page.locator('.tt-message.mine').filter({ hasText: 'Did the second check connect too?' }).waitFor()
          // Wait briefly: on baseline main, stream #1 stays stuck forever, so no Thinking indicator ever appears.
          await page.waitForTimeout(800)
          const awaitingCount = await page.locator('.awaiting').count()
          assert.equal(awaitingCount, 0, 'baseline reproduces missing Thinking indicator on dead socket')
          assert.equal(threadStreams.length, 1, 'baseline never reopened the dead thread stream')
          await shot('stalled')
          results.push({ width, theme, baseline: true, reproducedMissingIndicator: true, captures, errors })
          console.log('PASS baseline', width, theme)
          continue
        }

        // Fixed app: visibilitychange -> visible resynced the dead stream immediately!
        await waitFor(() => threadStreams.length === 2, 'visibilitychange resync stream')

        // Now send a message: the fresh stream receives turn.started and shows "Sprout · Thinking…"!
        await page.locator('#draft').click()
        await page.locator('#draft').fill('Did the second check connect too?')
        await page.getByTitle('Send', { exact: true }).click()
        await page.locator('.tt-message.mine').filter({ hasText: 'Did the second check connect too?' }).waitFor()
        await page.locator('.awaiting').filter({ hasText: 'Sprout · Thinking…' }).waitFor()
        await shot('thinking')

        // --- Scenario 2: Silent stall (no visibilitychange, no close) while a reply lands ---
        // Black-hole stream #2 on the server, complete the agent's reply on the broker, and advance clock past ping timeout.
        await page.clock.install()
        const beforeStall = threadStreams.length
        threadStreams.at(-1).blackholed = true
        // Suppress app server keepalive delivery by letting the browser clock advance 28s instantaneously.
        threadState.turns = []
        threadState.members[0].working = false
        threadState.members[0].busy = false
        const reply1 = {
          id: `m${threadState.nextId++}`,
          author: 'sprout',
          kind: 'message',
          engine: 'claude-code',
          model: 'claude-sonnet-4-6',
          at: nowIso(),
          text: 'Yes — both checks finished cleanly and the reply landed while the stream was quiet.',
          did: [],
        }
        threadState.messages.push(reply1)

        await page.clock.runFor(28000)
        await waitFor(() => threadStreams.length > beforeStall, 'heartbeat timeout reconnect')
        await page.clock.resume()
        await page.locator('.tt-message').filter({ hasText: reply1.text }).waitFor()
        assert.equal(await page.locator('.awaiting').count(), 0, 'thinking line cleared once reply arrived')
        await shot('reply')

        // --- Scenario 3: Server down -> calm honest notice with Try again -> recovery ---
        threadState.streamStatus = 503
        for (const s of threadStreams) s.drop()
        const notice = page.locator('.tt-connection')
        await notice.filter({ hasText: 'Can’t reach the server — still trying.' }).waitFor()
        const retryBtn = notice.getByRole('button', { name: 'Try again', exact: true })
        await retryBtn.waitFor()
        // Verify alignment with the composer box on both phone and desktop.
        const boxGeom = await page.evaluate(() => {
          const n = document.querySelector('.tt-connection').getBoundingClientRect()
          const c = document.querySelector('.composer-box').getBoundingClientRect()
          return { nLeft: Math.round(n.left), cLeft: Math.round(c.left), nRight: Math.round(n.right), cRight: Math.round(c.right) }
        })
        assert.ok(boxGeom.nLeft >= boxGeom.cLeft - 2 && boxGeom.nRight <= boxGeom.cRight + 2, `notice aligns with composer column: ${JSON.stringify(boxGeom)}`)
        await shot('disconnected')

        // Restore server with a second reply waiting and click Try again.
        threadState.streamStatus = 200
        const reply2 = {
          id: `m${threadState.nextId++}`,
          author: 'sprout',
          kind: 'message',
          engine: 'claude-code',
          model: 'claude-sonnet-4-6',
          at: nowIso(),
          text: 'Reconnected cleanly without leaving the conversation.',
          did: [],
        }
        threadState.messages.push(reply2)
        await retryBtn.click()
        await page.locator('.tt-message').filter({ hasText: reply2.text }).waitFor()
        await notice.waitFor({ state: 'detached' })

        // --- Scenario 4: offline -> calm offline notice -> online automatic recovery ---
        const beforeOffline = threadStreams.length
        threadStreams.at(-1).blackholed = true
        const reply3 = {
          id: `m${threadState.nextId++}`,
          author: 'sprout',
          kind: 'message',
          engine: 'claude-code',
          model: 'claude-sonnet-4-6',
          at: nowIso(),
          text: 'Caught up as soon as the network came back.',
          did: [],
        }
        threadState.messages.push(reply3)
        await page.evaluate(() => window.dispatchEvent(new Event('offline')))
        await notice.filter({ hasText: 'Offline — updates will resume when you’re back online.' }).waitFor()
        await page.evaluate(() => window.dispatchEvent(new Event('online')))
        await waitFor(() => threadStreams.length > beforeOffline, 'online resync')
        await page.locator('.tt-message').filter({ hasText: reply3.text }).waitFor()
        await notice.waitFor({ state: 'detached' })

        // --- Scenario 5: pageshow (bfcache restore) catches a reply landing while frozen ---
        const beforePageshow = threadStreams.length
        threadStreams.at(-1).blackholed = true
        const reply4 = {
          id: `m${threadState.nextId++}`,
          author: 'sprout',
          kind: 'message',
          engine: 'claude-code',
          model: 'claude-sonnet-4-6',
          at: nowIso(),
          text: 'Caught up immediately after pageshow.',
          did: [],
        }
        threadState.messages.push(reply4)
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })))
        await waitFor(() => threadStreams.length > beforePageshow, 'pageshow resync')
        await page.locator('.tt-message').filter({ hasText: reply4.text }).waitFor()

        // Confirm zero full thread reloads happened during any of the recoveries.
        assert.equal(threadReads, readsAfterOpen, 'no full thread GET reload during recovery')
        assert.deepEqual(errors, [])

        results.push({
          width,
          theme,
          baseline: false,
          visibilityResynced: true,
          thinkingVisibleAfterResync: true,
          silentStallRecovered: true,
          disconnectedNoticeCalmAndAligned: boxGeom,
          tryAgainRecovered: true,
          offlineOnlineRecovered: true,
          pageshowRecovered: true,
          threadReadsDuringRecovery: threadReads - readsAfterOpen,
          captures,
          errors,
        })
        console.log('PASS', width, theme)
      } finally {
        await context.close()
        await app.close()
      }
    }
  }
  if (out) {
    await writeFile(
      path.join(out, `${baseline ? 'before' : 'after'}-results.json`),
      JSON.stringify({ browser: browser.version(), baseline, results }, null, 2) + '\n',
    )
  }
} finally {
  await browser.close()
}
