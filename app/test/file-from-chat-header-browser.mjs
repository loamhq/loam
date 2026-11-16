// Actual page/relay in Chromium; synthetic notes and conversation, no host data.
import assert from 'node:assert/strict'
import path from 'node:path'
import { mkdir, writeFile } from 'node:fs/promises'
import { once } from 'node:events'
import { createApp } from '../src/server.js'
import { readConfig } from '../src/config.js'
import { fakeBroker, TOKEN, closing } from './harness.js'
import { filesHost } from './files-host.js'
import { row, listAnswer, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const output = process.env.FILE_HEADER_EVIDENCE || path.join(process.env.TMPDIR || '.', 'file-from-chat-header')
await mkdir(output, { recursive: true })
const folder = 'Projects/Studio planning'
const file = `${folder}/A little room to think.md`
const note = `# A little room to think

A shared plan for a calmer week. Keep the work visible, and leave room for the unexpected.

## The week ahead

| Workstream | What we are making | Next step | Who is involved | Delivery window | What success looks like |
| --- | --- | --- | --- | --- | --- |
| Research | A thoughtful guide to the next release | Review the first draft together | Design and engineering | Early next week | Everyone can find a clear way forward |
| Product | A quieter place to focus on the work | Make space for the important details | The whole studio | Later this month | A useful change that feels natural |
| Operations | A simple rhythm for sharing progress | Write a short weekly update | Project leads | Every Friday | Less catching up, more time to create |

## A small reminder

Good work needs a little breathing room. The plan can change as we learn more.
`
const record = {
  session_id: 'header-chat', agent: 'scout', provider: 'claude-code',
  title: 'Making room for the week', last_message: 'The plan is ready to read.',
  created: new Date().toISOString(), last_activity: new Date().toISOString(),
  last_event_at: new Date().toISOString(), state: 'exited', status: 'done', live: false,
  record_seq: 36, seen_seq: 36, background_tasks: 0, turns: [], pending_permissions: [], pinned: false, archived: false,
}
const events = Array.from({ length: 12 }, (_, i) => [
  { type: 'broker.user_message', seq: i * 3 + 1, session_id: record.session_id, text: `Planning note ${i + 1}: what should we make room for this week?` },
  { type: 'assistant', seq: i * 3 + 2, session_id: record.session_id, message: { id: `reply-${i}`, role: 'assistant', content: [{ type: 'text', text: `## A little progress, ${i + 1}\n\n${'We have a useful starting point. Keep the next step small, share what changed, and leave space to think.\n\n'.repeat(i < 2 ? 6 : 3)}${i === 1 ? `You can read [[${file}|the studio plan]] here.` : ''}` }] } },
  { type: 'result', seq: i * 3 + 3, session_id: record.session_id, subtype: 'success', is_error: false },
]).flat()
const json = (res, body) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
const sse = (res, body) => res.write(`data: ${JSON.stringify(body)}\n\n`)
const { routes } = filesHost()
routes['GET /v1/sessions'] = (req, res) => json(res, { sessions: [record], live_count: 0, live: 0, cap: 2 })
routes['GET /v1/sessions/events'] = (req, res) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(': open ping=15000\n\n')
  sse(res, { type: 'broker.session', ...record })
}
routes['GET /v1/sessions/header-chat/events'] = (req, res, url) => {
  const before = Number(url.searchParams.get('before'))
  const from = url.searchParams.get('from')
  const first = before || from !== null ? 1 : 7
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(': open ping=15000\n\n')
  sse(res, { type: 'broker.session', ...record, ...(from === null ? { window_first_seq: first, turns_before: first === 1 ? 0 : 2 } : {}) })
  for (const event of events.filter((e) => before ? e.seq < before : from !== null ? e.seq > Number(from) : e.seq >= first)) sse(res, event)
  if (before) res.end()
}
routes['PATCH /v1/sessions/header-chat'] = (req, res) => json(res, record)
routes['GET /v1/files/stat'] = (req, res, url) => json(res, row('vault', url.searchParams.get('path')))
routes['GET /v1/files/list'] = (req, res) => json(res, listAnswer(row('vault', folder), [row('vault', file)]))
routes['GET /v1/files/read'] = (req, res) => json(res, readAnswer(row('vault', file), note, 'sha256:header-fixture'))
routes['GET /v1/files/resolve'] = (req, res) => json(res, { status: 'found', resource: row('vault', file), fragment: null })
const broker = await fakeBroker(routes)
const speech = { async status() { return { available: true } }, stop() {} }
const { server } = createApp({ config: readConfig({ BROKER_URL: broker.url, BROKER_TOKEN: TOKEN, LOG_LEVEL: 'off' }), speech })
server.listen(0, '127.0.0.1')
await once(server, 'listening')
const url = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
const results = []
try {
  for (const width of [360, 390, 412, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme, isMobile: width < 1024, hasTouch: width < 1024 })
    const page = await context.newPage(), errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    const capture = async (name) => {
      await page.evaluate(() => document.fonts.ready)
      await page.screenshot({ path: path.join(output, `${name}-${width}-${theme}.png`) })
    }
    const geometry = async () => page.locator('.fs-edhead').evaluate((head) => {
      const rect = (n) => { const r = n.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height } }
      return {
        head: rect(head), title: rect(document.querySelector('.fs-edtitle')),
        buttons: [...head.children].filter((n) => getComputedStyle(n).display !== 'none' && n.matches('button, a, .segmented')).map((n) => ({ text: n.textContent || n.getAttribute('aria-label') || n.title, ...rect(n) })),
        pageOverflow: document.documentElement.scrollWidth > innerWidth,
      }
    })
    const check = (g) => {
      assert.equal(g.pageOverflow, false)
      assert.ok(g.title.y - g.head.bottom >= 15, 'title breathing room')
      for (const [i, b] of g.buttons.entries()) {
        assert.ok(b.x >= g.head.x && b.right <= g.head.right + 1, JSON.stringify(b))
        assert.ok(b.height >= 40, 'header touch target')
        if (i) assert.ok(b.x >= g.buttons[i - 1].right, 'header controls overlap')
      }
    }
    try {
      await page.goto(url)
      await page.locator('.srow').first().click()
      await page.locator('.thread-load').waitFor({ state: 'detached' })
      await page.locator('.earlier').click()
      const link = page.locator('.msgs .filelink').filter({ hasText: 'the studio plan' })
      await link.waitFor()
      await page.evaluate(() => document.fonts.ready)
      await link.evaluate((n) => n.scrollIntoView({ block: 'center' }))
      const beforeTop = await page.locator('.msgs').evaluate((n) => n.scrollTop)
      assert.ok(beforeTop > 200, `${width} ${theme}: exercise a real reading position away from either end, got ${beforeTop}`)
      const beforeCount = await page.locator('.msgs .line.right').count()
      await capture('chat-before')
      await link.click()
      await page.locator('.fs-preview .tablewrap[data-edges=right]').waitFor()
      const chatFile = await geometry()
      check(chatFile)
      if (width < 1024) {
        assert.equal(await page.locator('.fs-edhead .fs-back').count(), 0)
        assert.equal(await page.locator('.fs-edhead .fs-chatback').innerText(), 'Chat')
        assert.equal(await page.locator('.fs-location').innerText(), `Vault / ${folder}`)
      }
      await capture('from-chat')
      if (width < 1024) {
        await page.locator('.fs-filemenu').click()
        await page.locator('.fs-menu-folder').waitFor()
        await page.locator('.fs-sheet .sheettop .iconbtn').click()
        await page.locator('.fs-sheet').waitFor({ state: 'detached' })
      }
      const table = page.locator('.fs-preview .tablewrap')
      const tableSize = await table.evaluate((n) => ({ width: n.clientWidth, content: n.scrollWidth, mask: getComputedStyle(n).maskImage, previewWidth: n.closest('.fs-preview').clientWidth, previewContent: n.closest('.fs-preview').scrollWidth }))
      assert.ok(tableSize.content > tableSize.width, 'wide table must scroll independently')
      assert.equal(tableSize.previewWidth, tableSize.previewContent, 'preview itself must not scroll sideways')
      assert.match(tableSize.mask, /linear-gradient/)
      await table.focus()
      await page.keyboard.press('ArrowRight')
      await page.waitForFunction(() => document.querySelector('.tablewrap').scrollLeft > 0)
      await table.evaluate((n) => { n.scrollLeft = (n.scrollWidth - n.clientWidth) / 2 })
      await page.locator('.tablewrap[data-edges=both]').waitFor()
      await capture('wide-table-middle')
      await table.evaluate((n) => { n.scrollLeft = n.scrollWidth })
      await page.locator('.tablewrap[data-edges=left]').waitFor()
      await capture('wide-table-end')
      // The compact toggle gives way first on a narrow file pane.
      const mode = width <= 380 ? page.locator('.fs-mode-toggle') : page.locator('.segmented').getByRole('button', { name: 'Edit', exact: true })
      await mode.click()
      await page.locator('.fs-field').waitFor()
      check(await geometry())
      await (width <= 380 ? page.locator('.fs-mode-toggle') : page.locator('.segmented').getByRole('button', { name: 'Preview', exact: true })).click()
      await page.locator('.fs-preview').waitFor()
      if (width < 1024) await page.locator('.fs-chatback').click()
      else await page.locator('.railtab[title=Chats]').click()
      await page.locator('.msgs .filelink').waitFor()
      const afterTop = await page.locator('.msgs').evaluate((n) => n.scrollTop)
      assert.ok(Math.abs(afterTop - beforeTop) <= 1, `scroll moved: ${beforeTop} -> ${afterTop}`)
      assert.equal(await page.locator('.msgs .line.right').count(), beforeCount, 'earlier pages retained')
      await capture('chat-returned')
      assert.ok(broker.seen.some((r) => r.path === '/v1/sessions/header-chat/events' && r.search === '?from=36'), 'return reconnects at received event')
      // Visit the folder, then this same file: it must use folder back even
      // though the Files visit still knows which chat it came from.
      await page.locator('.msgs .filelink').click()
      await page.locator('.fs-preview').waitFor()
      if (width < 1024) await page.locator('.fs-location').click()
      await page.locator('.setrow').filter({ hasText: 'A little room to think.md' }).click()
      await page.locator('.fs-preview').waitFor()
      if (width < 1024) {
        assert.equal(await page.locator('.fs-chatback').count(), 0)
        assert.equal(await page.locator('.fs-edhead .fs-back').innerText(), 'Studio planning')
      }
      const filesFile = await geometry()
      check(filesFile)
      await capture('from-files')
      if (width < 1024) {
        await page.locator('.fs-edhead .fs-back').click()
        await page.locator('.setrow').filter({ hasText: 'A little room to think.md' }).waitFor()
        assert.equal(await page.locator('.fs-preview').count(), 0)
      }
      assert.deepEqual(errors, [])
      results.push({ width, theme, chatFile, filesFile, beforeTop, afterTop, retainedTurns: beforeCount, tableSize, pageErrors: errors })
    } catch (error) {
      await page.screenshot({ path: path.join(output, `failure-${width}-${theme}.png`) })
      console.error(await page.locator('.msgs').evaluate((n) => ({ top: n.scrollTop, height: n.scrollHeight, client: n.clientHeight, text: n.textContent.slice(0, 1200) })).catch(() => null))
      throw error
    } finally { await context.close() }
  }
  await writeFile(path.join(output, 'browser.json'), JSON.stringify({ browser: browser.version(), fixtures: true, results }, null, 2) + '\n')
  console.log(JSON.stringify({ contexts: results.length, captures: results.length * 6, browserErrors: 0, earlierHistoryAndScrollRetained: true }))
} finally { await browser.close(); await closing({ server }, broker)() }
