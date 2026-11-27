// Browser regression test and before/after captures for chat tables (and
// Files preview table consistency) at 390x844 (phone/touch) and 1440x900
// (desktop/hover), in light and dark Latte.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   CHAT_TABLES_EVIDENCE=/tmp/out node test/chat-tables-browser.mjs
//
// Pass CHAT_TABLES_BASELINE=1 against an archive of main to capture the
// pre-change baseline screenshots and metrics.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { row, listAnswer, readAnswer } from './fixtures/files-contract.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.CHAT_TABLES_EVIDENCE
const baseline = process.env.CHAT_TABLES_BASELINE === '1'
if (out) await mkdir(out, { recursive: true })

const at = '2026-11-26T18:00:00Z'
const threadId = 'a-quieter-week'

const proseTableMd = [
  '| Operation | Time | What happened |',
  '| --- | --- | --- |',
  '| Build image | 4 min 10 s | Rebuilt the worker image and pushed layers to the registry. |',
  '| Health check | 18 s | Waited for the new process to pass readiness probes. |',
  '| Switch traffic | 2 s | Moved incoming requests to the fresh release cleanly. |',
].join('\n')

const numericTableMd = [
  '| Service | Req/s | p50 | p99 | Errors | Monthly |',
  '| --- | --- | --- | --- | --- | --- |',
  '| api-edge | 1,420 | 18 ms | 94 ms | 0.02% | $14.20 |',
  '| worker-queue | 14,280 | 142 ms | 890 ms | 0.14% | $142.80 |',
  '| media-proxy | 610 | 31 ms | 210 ms | 0.00% | $8.50 |',
].join('\n')

const twoColTableMd = [
  '| Setting | Value |',
  '| --- | --- |',
  '| Appearance | Latte |',
  '| Font size | 100% |',
  '| Port | 8080 |',
].join('\n')

const ownerTableMd = [
  'Here is the split I had in mind for this week:',
  '',
  '| Track | Owner | Target |',
  '| --- | --- | --- |',
  '| Tables | Sam | Thu 4 pm |',
  '| Docs | Sprout | Fri noon |',
].join('\n')

const urlCodeTableMd = [
  '| Check | Target | Status |',
  '| --- | --- | --- |',
  '| Readiness | [https://status.example.com/v2/clusters/us-west-2/deployments/2026-11-26/health-report](https://status.example.com/v2/clusters/us-west-2/deployments/2026-11-26/health-report) | Healthy |',
  '| Preview OOM | `APP_PREVIEW_OOM_SCORE_ADJ` set to `300` in service environment | Active |',
].join('\n')

const previewNoteMd = [
  '# Release plan',
  '',
  '| Workstream | What we are making | Next step | Who is involved | Delivery window | What success looks like |',
  '| --- | --- | --- | --- | --- | --- |',
  '| Research | A thoughtful guide to the next release | Review the first draft together | Design and engineering | Early next week | Everyone can find a clear way forward |',
  '| Operations | A simple rhythm for sharing progress | Write a short weekly update | Project leads | Every Friday | Less catching up, more time to create |',
].join('\n')

const messages = [
  { id: 'm1', author: 'you', kind: 'message', at, text: 'Can you summarize the rollout timing, service numbers, settings, and checks?' },
  { id: 'm2', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [], text: `Here is how the rollout went:\n\n${proseTableMd}` },
  { id: 'm3', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [], text: `Current service figures across the cluster:\n\n${numericTableMd}` },
  { id: 'm4', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [], text: `Active defaults for this workspace:\n\n${twoColTableMd}` },
  { id: 'm5', author: 'you', kind: 'message', at, text: ownerTableMd },
  { id: 'm6', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [], text: `Endpoints and flags verified after cutover:\n\n${urlCodeTableMd}` },
]

const metrics = []
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  for (const width of [390, 1440]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await oneProductFixture()
    const record = await app.f.api('GET', `/threads/${threadId}`)
    record.thread.unread = 0
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    Object.assign(app.routes, filesHost().routes)
    app.routes['GET /v1/files/stat'] = (req, res, url) => json(res, row('vault', url.searchParams.get('path') || 'Plan.md'))
    app.routes['GET /v1/files/list'] = (req, res) => json(res, listAnswer(row('vault', ''), [row('vault', 'Plan.md')]))
    app.routes['GET /v1/files/read'] = (req, res) => json(res, readAnswer(row('vault', 'Plan.md'), previewNoteMd, 'sha256:plan-fixture'))
    app.routes[`GET /v1/threads/${threadId}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${threadId}/steps`] = (req, res) => json(res, { error: 'No full record' }, 404)
    const appearance = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: appearance, desktop: appearance })

    const context = await browser.newContext({
      viewport: { width, height: wide ? 900 : 844 },
      colorScheme: mode,
      isMobile: !wide,
      hasTouch: !wide,
      reducedMotion: 'reduce',
    })
    const page = await context.newPage()
    const errors = []
    page.setDefaultTimeout(15000)
    page.on('pageerror', (e) => errors.push(e.message))
    await page.addInitScript(() => {
      window.__copied = []
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (text) => { window.__copied.push(text) } },
      })
    })

    const settle = async () => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => {
        document.activeElement?.blur()
        await document.fonts.ready
        await new Promise(requestAnimationFrame)
        await new Promise(requestAnimationFrame)
      })
      await page.waitForTimeout(200)
    }

    const scrollToMessage = async (mid, offset = 58) => {
      await page.locator(`[data-message-id="${mid}"]`).evaluate((node, off) => {
        const pane = node.closest('.tt-stream')
        if (pane) pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - off
      }, offset)
      await settle()
    }

    const tag = `${baseline ? 'before' : 'after'}-${width}-${mode}`
    try {
      await page.goto(`${app.url}/threads/${threadId}`)
      await page.locator('[data-message-id="m6"]').waitFor()
      for (const mid of ['m2', 'm3', 'm4', 'm5', 'm6']) {
        await scrollToMessage(mid)
        await page.locator(`[data-message-id="${mid}"] table`).waitFor()
      }

      // Expand owner bubble if clamped so the full owner table is visible in captures.
      const showMore = page.locator('[data-message-id="m5"] .morebtn')
      if (await showMore.count()) {
        await scrollToMessage('m5', 80)
        await showMore.click()
        await settle()
      }

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      assert.equal(overflow, false, `${tag}: page horizontal overflow`)

      const measured = await page.evaluate(() => {
        const readTable = (mid) => {
          const msg = document.querySelector(`[data-message-id="${mid}"]`)
          const wrap = msg.querySelector('.tablewrap')
          const frame = msg.querySelector('.table-frame')
          const table = wrap.querySelector('table')
          const th = table.querySelector('thead th')
          const td = table.querySelector('tbody td')
          const copy = frame?.querySelector('.copyblk')
          const firstRowCells = [...table.querySelectorAll('tbody tr:first-child td')].map((cell) => {
            const range = document.createRange()
            range.selectNodeContents(cell)
            return {
              text: cell.textContent,
              width: Math.round(cell.getBoundingClientRect().width),
              lines: range.getClientRects().length,
              whiteSpace: getComputedStyle(cell).whiteSpace,
            }
          })
          const tableStyle = getComputedStyle(table)
          const thStyle = getComputedStyle(th)
          const tdStyle = getComputedStyle(td)
          const row2Td = table.querySelector('tbody tr:nth-child(2) td')
          return {
            mid,
            framePaddingTop: frame ? parseFloat(getComputedStyle(frame).paddingTop) : 0,
            frameW: frame ? Math.round(frame.getBoundingClientRect().width) : Math.round(wrap.getBoundingClientRect().width),
            clientW: wrap.clientWidth,
            scrollW: wrap.scrollWidth,
            tableW: Math.round(table.getBoundingClientRect().width),
            edges: wrap.getAttribute('data-edges'),
            mask: getComputedStyle(wrap).maskImage || getComputedStyle(wrap).webkitMaskImage,
            borderRadius: getComputedStyle(wrap).borderRadius,
            fontSize: parseFloat(tableStyle.fontSize),
            lineHeight: parseFloat(tableStyle.lineHeight),
            tabular: tableStyle.fontVariantNumeric,
            thFontSize: parseFloat(thStyle.fontSize),
            thWeight: Number(thStyle.fontWeight),
            thBg: thStyle.backgroundColor,
            thBorderBottom: thStyle.borderBottomWidth,
            tdPaddingTop: parseFloat(tdStyle.paddingTop),
            tdPaddingBottom: parseFloat(tdStyle.paddingBottom),
            rowRuleWidth: row2Td ? getComputedStyle(row2Td).borderTopWidth : null,
            copyOpacity: copy ? parseFloat(getComputedStyle(copy).opacity) : null,
            copyPointer: copy ? getComputedStyle(copy).pointerEvents : null,
            firstRowCells,
          }
        }
        return ['m2', 'm3', 'm4', 'm5', 'm6'].map(readTable)
      })

      metrics.push({ tag, width, mode, tables: measured })

      // Capture views covering all 5 table scenarios.
      await scrollToMessage('m2', 58)
      if (out) await page.screenshot({ path: path.join(out, `${tag}-prose-numeric.png`) })

      await scrollToMessage('m4', 58)
      if (out) await page.screenshot({ path: path.join(out, `${tag}-twocol-owner.png`) })

      await scrollToMessage('m6', 58)
      if (out) await page.screenshot({ path: path.join(out, `${tag}-url-code.png`) })

      if (!baseline) {
        const byId = Object.fromEntries(measured.map((t) => [t.mid, t]))

        // 1. 3-column prose table fits without horizontal scroll; "4 min 10 s" does not break.
        assert.ok(byId.m2.scrollW <= byId.m2.clientW + 1, `${tag} m2 should fit column: ${byId.m2.scrollW} <= ${byId.m2.clientW}`)
        assert.equal(byId.m2.edges, 'none', `${tag} m2 edges`)
        assert.equal(byId.m2.firstRowCells[1].text, '4 min 10 s')
        assert.equal(byId.m2.firstRowCells[1].lines, 1, `${tag} m2 "4 min 10 s" broke across lines`)
        assert.ok(byId.m2.firstRowCells[2].width >= 130, `${tag} m2 prose column collapsed: ${byId.m2.firstRowCells[2].width}px`)

        // 2. 6-column numeric table scrolls at 390 with edge fades, fits at 1440.
        if (!wide) {
          assert.ok(byId.m3.scrollW > byId.m3.clientW + 1, `${tag} m3 should scroll at 390: ${byId.m3.scrollW} vs ${byId.m3.clientW}`)
          assert.equal(byId.m3.edges, 'right', `${tag} m3 should show right edge fade`)
          assert.match(byId.m3.mask, /linear-gradient/, `${tag} m3 should have mask-image fade`)

          // Scroll partway and to end to verify both and left edge masks.
          const m3Wrap = page.locator('[data-message-id="m3"] .tablewrap')
          await scrollToMessage('m3', 64)
          await m3Wrap.evaluate((n) => { n.scrollLeft = Math.round((n.scrollWidth - n.clientWidth) / 2) })
          await page.locator('[data-message-id="m3"] .tablewrap[data-edges="both"]').waitFor()
          if (out) await page.screenshot({ path: path.join(out, `${tag}-numeric-mid.png`) })
          await m3Wrap.evaluate((n) => { n.scrollLeft = n.scrollWidth })
          await page.locator('[data-message-id="m3"] .tablewrap[data-edges="left"]').waitFor()
          await m3Wrap.evaluate((n) => { n.scrollLeft = 0 })

          // Touch swipe on scrollable table must not trigger message swipe-to-reply.
          const swipeResult = await page.evaluate(() => {
            const msg = document.querySelector('[data-message-id="m3"]')
            const cell = msg.querySelector('tbody td')
            const r = cell.getBoundingClientRect()
            const fire = (type, x, y) => cell.dispatchEvent(new PointerEvent(type, {
              bubbles: true, cancelable: true, pointerType: 'touch', isPrimary: true, pointerId: 1, clientX: x, clientY: y,
            }))
            fire('pointerdown', r.left + 10, r.top + 10)
            fire('pointermove', r.left + 80, r.top + 10)
            const dragging = msg.classList.contains('reply-dragging')
            fire('pointerup', r.left + 80, r.top + 10)
            return { dragging }
          })
          assert.equal(swipeResult.dragging, false, `${tag} touch swipe on table triggered reply-dragging`)
        } else {
          assert.ok(byId.m3.scrollW <= byId.m3.clientW + 1, `${tag} m3 should fit at 1440: ${byId.m3.scrollW} <= ${byId.m3.clientW}`)
          assert.equal(byId.m3.edges, 'none', `${tag} m3 edges at 1440`)
        }

        // 3. 2-column table fits cleanly and does not over-stretch across 760px on desktop.
        assert.ok(byId.m4.scrollW <= byId.m4.clientW + 1, `${tag} m4 should fit`)
        if (wide) assert.ok(byId.m4.frameW < 520, `${tag} m4 over-stretched on desktop: ${byId.m4.frameW}px`)

        // 4. Owner bubble table fits and keeps transparent header + colour-mix rules.
        assert.ok(byId.m5.scrollW <= byId.m5.clientW + 1, `${tag} m5 owner table should fit`)
        assert.equal(byId.m5.thBg, 'rgba(0, 0, 0, 0)', `${tag} m5 header should have no fill`)
        assert.equal(byId.m5.thBorderBottom, '1px', `${tag} m5 header should have bottom rule`)
        assert.equal(byId.m5.rowRuleWidth, '1px', `${tag} m5 body rows should have hairline`)

        // 5. Long URL / code cell wraps without awkward scroll off screen.
        assert.ok(byId.m6.scrollW <= byId.m6.clientW + 1, `${tag} m6 URL/code table should fit: ${byId.m6.scrollW} <= ${byId.m6.clientW}`)

        // 6. Calm typography & spacing.
        assert.equal(byId.m2.fontSize, 15, `${tag} body font size`)
        assert.ok(byId.m2.lineHeight >= 21 && byId.m2.lineHeight <= 22.5, `${tag} body line-height: ${byId.m2.lineHeight}`)
        assert.match(byId.m2.tabular, /tabular-nums/, `${tag} tabular-nums`)
        assert.equal(byId.m2.thFontSize, 12.5, `${tag} header font size`)
        assert.equal(byId.m2.thWeight, 600, `${tag} header font weight`)
        assert.equal(byId.m2.thBg, 'rgba(0, 0, 0, 0)', `${tag} header no fill`)
        assert.equal(byId.m2.borderRadius, '0px', `${tag} no rounded box`)
        assert.ok(byId.m2.tdPaddingTop >= 9 && byId.m2.tdPaddingBottom >= 9, `${tag} vertical row padding`)

        // 7. Touch vs Desktop Copy button.
        if (!wide) {
          assert.equal(byId.m2.framePaddingTop, 0, `${tag} touch table must not have 52px top band`)
          assert.equal(byId.m2.copyOpacity, 0, `${tag} touch table copy pill must be hidden`)
          assert.equal(byId.m2.copyPointer, 'none', `${tag} hidden touch table copy must not block taps`)
        } else {
          assert.equal(byId.m2.copyOpacity, 0, `${tag} desktop copy pill hidden at rest`)
          await scrollToMessage('m2', 58)
          await page.locator('[data-message-id="m2"] .table-frame').hover()
          await page.waitForTimeout(200)
          const hoverCopy = await page.locator('[data-message-id="m2"] .table-frame .copyblk').evaluate((n) => ({
            opacity: parseFloat(getComputedStyle(n).opacity),
            pointer: getComputedStyle(n).pointerEvents,
          }))
          assert.equal(hoverCopy.opacity, 1, `${tag} desktop hover reveals Copy button`)
          assert.equal(hoverCopy.pointer, 'auto', `${tag} desktop hover enables Copy button`)
          if (out) await page.screenshot({ path: path.join(out, `${tag}-desktop-hover-copy.png`) })
          await page.locator('[data-message-id="m2"] .table-frame .copyblk').click()
          const copied = await page.evaluate(() => window.__copied.at(-1))
          assert.equal(copied, proseTableMd, `${tag} desktop Copy copied exact markdown table`)
        }

        // 8. Files preview (.fs-preview) wide table keeps its edge masks and calm header.
        await page.goto(`${app.url}/files/vault/Plan.md`)
        const fsWrap = page.locator('.fs-preview .tablewrap')
        await fsWrap.waitFor()
        await settle()
        await page.locator('.fs-preview .tablewrap[data-edges="right"]').waitFor()
        const fsCheck = await fsWrap.evaluate((w) => ({
          scrollW: w.scrollWidth,
          clientW: w.clientWidth,
          mask: getComputedStyle(w).maskImage || getComputedStyle(w).webkitMaskImage,
          thBg: getComputedStyle(w.querySelector('thead th')).backgroundColor,
        }))
        assert.ok(fsCheck.scrollW > fsCheck.clientW + 1, `${tag} Files preview wide table scrolls`)
        assert.match(fsCheck.mask, /linear-gradient/, `${tag} Files preview edge fade mask`)
        assert.equal(fsCheck.thBg, 'rgba(0, 0, 0, 0)', `${tag} Files preview shares calm header`)
        await fsWrap.evaluate((w) => { w.scrollLeft = Math.round((w.scrollWidth - w.clientWidth) / 2) })
        await page.locator('.fs-preview .tablewrap[data-edges="both"]').waitFor()
        await fsWrap.evaluate((w) => { w.scrollLeft = w.scrollWidth })
        await page.locator('.fs-preview .tablewrap[data-edges="left"]').waitFor()
      }

      assert.deepEqual(errors, [], `${tag}: page errors: ${errors.join('; ')}`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}

if (out) {
  await writeFile(path.join(out, `${baseline ? 'before' : 'after'}-metrics.json`), JSON.stringify(metrics, null, 2))
}
console.log(JSON.stringify(metrics.map(({ tag, tables }) => ({
  tag,
  tables: tables.map(({ mid, clientW, scrollW, framePaddingTop, copyOpacity, edges, firstRowCells }) => ({
    mid, clientW, scrollW, framePaddingTop, copyOpacity, edges,
    cols: firstRowCells.map((c) => `${c.width}px/${c.lines}L`),
  })),
})), null, 2))
