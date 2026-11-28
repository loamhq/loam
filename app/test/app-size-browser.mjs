// Browser regression test and before/after captures for app sizing:
// full-width main surfaces at every size, 0.9-base default (100% = --ui-scale 0.9),
// and usable, composed 60%–200% layouts across Chats (with 7 agents and 4+
// header actions), Thread (long message + composer with mic & send), Files,
// Settings > Appearance, and pop-up sheets at 390x844 and 1440x900, light and dark.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   APP_SIZE_EVIDENCE=docs/evidence/app-size node test/app-size-browser.mjs
//
// Pass APP_SIZE_BASELINE=1 against a git archive of main to capture pre-change
// baseline screenshots of the 200% issues.
import assert from 'node:assert/strict'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { mergeAppearance } from '../public/theme-options.js'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.APP_SIZE_EVIDENCE
const baseline = process.env.APP_SIZE_BASELINE === '1'
if (out) await mkdir(out, { recursive: true })

const LONG_REPLY = [
  '## Weekly synthesis and next steps',
  '',
  'Here is the full recap after walking through the notes, the open checklist items, and the sizing adjustments we discussed earlier today. Every paragraph should wrap cleanly inside the reading column at 60%, 100%, and 200% without forcing horizontal scroll or pushing controls off-screen.',
  '',
  '- **Full-width surfaces:** Remove the legacy 460px ceiling on the app shell so Chats, Threads, Files, Apps, and the bottom tab bar always reach the viewport edges when zoomed out.',
  '- **Calibrated default:** Make 100% correspond to 0.9 of the previous scale (`--ui-scale: 0.9`), keeping the layout calm and airy while letting users scale from 60% up to 200%.',
  '- **High-zoom usability:** Keep the Chats header composed, balance the agent faces grid across the row, and keep Send beside the microphone at the right of the composer.',
  '',
  'Inline code such as `sizeToScale(100) === 0.9` and a longer token like `appearance.phone.size=60..200` should also stay contained inside the message column.',
].join('\n')

const app = await oneProductFixture()
// Seven synthetic agents to match real-world multi-agent grids at 60%, 100%, and 200%.
app.f.agents.push(
  { id: 'moss', name: 'Moss', engine: 'claude-code', color: '#4A6B5C', face_slot: 5 },
  { id: 'fern', name: 'Fern', engine: 'claude-code', color: '#6E5A7A', face_slot: 6 },
)
for (const [key, handler] of Object.entries(filesHost().routes)) {
  if (key.includes('/v1/files/')) app.routes[key] = handler
}

const listing = await app.f.api('GET', '/threads')
const nowMs = Date.now()
const ago = (mins) => new Date(nowMs - mins * 60000).toISOString()
const realTitleThreads = listing.threads.map((t, i) => {
  if (t.id === 'general') {
    return { ...t, title: 'General', last: { ...(t.last || {}), at: ago(125) } }
  }
  if (t.id === 'a-quieter-week') {
    return { ...t, title: 'Hosted Loam: sign up, pick a plan, get a box', last: { ...(t.last || {}), at: ago(1500) } }
  }
  if (i === 2) {
    return { ...t, title: 'Visuals inside agent replies', last: { ...(t.last || {}), at: ago(210) } }
  }
  return { ...t, last: { ...(t.last || {}), at: ago(3000) } }
})

const baseRecord = await app.f.api('GET', '/threads/a-quieter-week')
const longRecord = {
  ...baseRecord,
  thread: { ...baseRecord.thread, title: 'Hosted Loam: sign up, pick a plan, get a box' },
  messages: [
    ...baseRecord.messages,
    { id: 'm-long', author: baseRecord.thread.members[0].id, kind: 'message', at: '2026-11-27T17:10:00Z', text: LONG_REPLY },
  ],
}
const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
app.routes['GET /v1/threads'] = (req, res) => json(res, { ...listing, threads: realTitleThreads })
app.routes['GET /v1/threads/events'] = (req, res) => {
  res.writeHead(200, { 'content-type': 'text/event-stream' })
  res.write(`: open ping=15000\n\ndata: ${JSON.stringify({ type: 'threads.snapshot', threads: realTitleThreads, agents: app.f.agents })}\n\n`)
}
app.routes['GET /v1/threads/a-quieter-week'] = (req, res) => json(res, longRecord)

let refuseExtended = false
const values = { phone: { mode: 'light', size: 100, theme: 'latte' }, desktop: { mode: 'light', size: 100, theme: 'latte' } }
app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
  const patch = JSON.parse(raw)
  for (const [kind, fields] of Object.entries(patch)) {
    if (refuseExtended && fields?.size !== undefined && (fields.size < 75 || fields.size > 150)) {
      return json(res, { error: `${kind} size is a whole percent from 75 to 150 in steps of 5, not ${fields.size}` }, 400)
    }
  }
  for (const [kind, fields] of Object.entries(patch)) values[kind] = mergeAppearance(values[kind], fields)
  json(res, values)
}

// Register a synthetic plugin list action (like Board) and microphone button
// so the test exercises the real 4/5-button header and mic+send composer row.
const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8')
const instrumentedApp = appSource + `
;(() => {
  const fx = registry.scope({ id: 'fixture-polish' })
  fx.places['thread.list.action'].add({
    id: 'fixture-board',
    render: () => h('button', { class: 'iconbtn ghost', type: 'button', title: 'Board', 'aria-label': 'Board' }, icon.grid ? icon.grid(18) : icon.folder(18)),
  })
  fx.places.composer.tool.add({
    id: 'fixture-mic',
    slot: 'microphone',
    when: (context) => context.slot === 'microphone' && context.target === 'draft',
    render: () => h('button', { class: 'roundmic', type: 'button', title: 'Voice input', 'aria-label': 'Voice input', style: { borderRadius: '999px', border: '0' } }, icon.mic ? icon.mic(16) : icon.plus(16)),
  })
  schedule()
})()
`

const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] })

async function newPage(width, height, mode) {
  const isPhone = width < 1100
  const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: mode, isMobile: isPhone, hasTouch: isPhone, reducedMotion: 'reduce' })
  await ctx.route('**/app.js', (route) => route.fulfill({ contentType: 'text/javascript', body: instrumentedApp }))
  const page = await ctx.newPage()
  page.setDefaultTimeout(10000)
  return { ctx, page }
}

async function settle(page) {
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  })
}

async function measure(page, screen) {
  return page.evaluate((currentScreen) => {
    const W = innerWidth, H = innerHeight
    const appBox = document.querySelector('#app').getBoundingClientRect()
    const issues = []
    if (Math.abs(appBox.left) > 1.5 || Math.abs(appBox.right - W) > 1.5) {
      issues.push(`#app width mismatch: left=${appBox.left.toFixed(1)} right=${appBox.right.toFixed(1)} W=${W}`)
    }
    if (Math.abs(appBox.height - H) > 2) {
      issues.push(`#app height mismatch: h=${appBox.height.toFixed(1)} H=${H}`)
    }
    if (document.documentElement.scrollWidth > W + 1) {
      issues.push(`doc overflowX: ${document.documentElement.scrollWidth} > ${W}`)
    }
    if (document.documentElement.scrollHeight > H + 1) {
      issues.push(`doc overflowY: ${document.documentElement.scrollHeight} > ${H}`)
    }

    // 1. Chats list header stays on one composed row (no stranded button) and
    //    agent faces grid fills/centers its row symmetrically.
    let headerRows = null, agentBalance = null
    if (currentScreen === 'chats') {
      const head = document.querySelector('.tt-list .home-head, .tt-list .list-head')
      if (head) {
        const items = [...head.querySelectorAll('.display, button')].filter((el) => el.getBoundingClientRect().width > 0)
        const ys = items.map((el) => Math.round(el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2))
        const spread = Math.max(...ys) - Math.min(...ys)
        const title = head.querySelector('.display')
        const titleTruncated = title ? title.scrollWidth > title.clientWidth + 1 : false
        headerRows = { count: items.length, spread, titleTruncated }
        if (spread > 10) issues.push(`chats header wrapped across rows: spread=${spread}px`)
        if (titleTruncated) issues.push(`chats heading truncated: sw=${title.scrollWidth} cw=${title.clientWidth}`)
        for (const el of items) {
          const r = el.getBoundingClientRect()
          if (r.left < -1 || r.right > W + 1) issues.push(`header item out: ${r.left.toFixed(1)}..${r.right.toFixed(1)}`)
        }
      }
      const strip = document.querySelector('.tt-list .strip')
      if (strip) {
        const sr = strip.getBoundingClientRect()
        const personas = [...strip.querySelectorAll('.persona')].map((p) => p.getBoundingClientRect()).filter((r) => r.width > 0)
        if (personas.length) {
          const firstRowTop = Math.min(...personas.map((r) => r.top))
          const firstRow = personas.filter((r) => Math.abs(r.top - firstRowTop) < 10)
          const leftGap = Math.min(...firstRow.map((r) => r.left)) - sr.left
          const rightGap = sr.right - Math.max(...firstRow.map((r) => r.right))
          agentBalance = { firstRowCount: firstRow.length, leftGap: Math.round(leftGap), rightGap: Math.round(rightGap) }
          if (Math.abs(leftGap - rightGap) > 16) {
            issues.push(`agent strip unbalanced: leftGap=${leftGap.toFixed(1)} rightGap=${rightGap.toFixed(1)}`)
          }
        }
      }
      // Verify chat list rows: no mid-word breaks inside titles, generous title
      // width on narrow lists, and unchanged 80px row height at 60% and 100%.
      const scaleNum = Number(getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim()) || 1
      const listEl = document.querySelector('.tt-list')
      const listLayoutW = listEl ? listEl.getBoundingClientRect().width / scaleNum : 999
      const rowChecks = []
      for (const rowEl of document.querySelectorAll('.tt-list .tt-row')) {
        const rr = rowEl.getBoundingClientRect()
        const titleEl = rowEl.querySelector('.srow-title')
        if (!titleEl || !rr.width) continue
        const tr = titleEl.getBoundingClientRect()
        const textNode = titleEl.firstChild
        const brokenWords = []
        if (textNode && textNode.nodeType === 3) {
          const txt = textNode.textContent || ''
          const wordRe = /\S+/g
          let match
          while ((match = wordRe.exec(txt))) {
            const range = document.createRange()
            range.setStart(textNode, match.index)
            range.setEnd(textNode, match.index + match[0].length)
            const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5)
            // Ignore characters clipped out by -webkit-line-clamp beyond the box bottom.
            const visible = rects.filter((r) => r.top < tr.bottom - 1)
            const lines = new Set(visible.map((r) => Math.round(r.top / 4)))
            if (lines.size > 1) brokenWords.push(match[0])
          }
        }
        const share = tr.width / rr.width
        rowChecks.push({ title: titleEl.textContent, width: Math.round(tr.width), share: Number(share.toFixed(2)), brokenWords })
        if (brokenWords.length) issues.push(`mid-word break in "${titleEl.textContent}": ${brokenWords.join(', ')}`)
        if (listLayoutW <= 280 && share < 0.6) issues.push(`narrow row title too squeezed ("${titleEl.textContent}"): share=${share.toFixed(2)}`)
        if (listLayoutW > 280 && Math.abs(rr.height - 80 * scaleNum) > 1.5) {
          issues.push(`row height drifted at normal width ("${titleEl.textContent}"): ${rr.height.toFixed(1)} vs ${(80 * scaleNum).toFixed(1)}`)
        }
        const pin = rowEl.closest('.tt-row-wrap')?.querySelector('.tt-row-pin')
        if (pin && Number(getComputedStyle(pin).opacity) > 0.5) {
          const pr = pin.getBoundingClientRect()
          if (pr.left < tr.right - 1 && tr.left < pr.right - 1 && pr.top < tr.bottom - 1 && tr.top < pr.bottom - 1) {
            issues.push(`row pin overlaps title ("${titleEl.textContent}")`)
          }
        }
      }
      headerRows = { ...headerRows, rowChecks }
    }

    // 2. Composer: no overlap, send stays on right beside mic (never stranded on far left).
    let composerInfo = null
    const compBox = document.querySelector('.composer-box')
    const compBtns = [...document.querySelectorAll('.composer-actions button')].filter((b) => b.getBoundingClientRect().width > 0)
    if (compBox && compBtns.length) {
      const cbr = compBox.getBoundingClientRect()
      for (let i = 0; i < compBtns.length; i++) {
        const a = compBtns[i].getBoundingClientRect()
        if (a.left < -1 || a.right > W + 1) issues.push(`composer btn ${i} out`)
        if (compBtns[i].scrollWidth > compBtns[i].clientWidth + 2) issues.push(`composer btn ${i} inner clip`)
        for (let j = i + 1; j < compBtns.length; j++) {
          const b = compBtns[j].getBoundingClientRect()
          if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) {
            issues.push(`composer overlap ${i} & ${j}`)
          }
        }
      }
      const mic = document.querySelector('.composer-actions .roundmic')
      const send = document.querySelector('.composer-actions .roundsend')
      if (mic && send) {
        const mr = mic.getBoundingClientRect(), sr = send.getBoundingClientRect()
        const rightEdgeGap = cbr.right - sr.right
        const sameRow = Math.abs((mr.top + mr.height / 2) - (sr.top + sr.height / 2)) < 8
        const sendRightOfMic = sr.left >= mr.right - 1
        const allTops = compBtns.map((b) => Math.round(b.getBoundingClientRect().top + b.getBoundingClientRect().height / 2))
        const rowSpread = Math.max(...allTops) - Math.min(...allTops)
        composerInfo = { rightEdgeGap: Math.round(rightEdgeGap), sameRow, sendRightOfMic, rowSpread }
        if (!sameRow || !sendRightOfMic) issues.push(`send not beside mic: sameRow=${sameRow} sendRightOfMic=${sendRightOfMic}`)
        if (rightEdgeGap > 24) issues.push(`send not on right edge: rightEdgeGap=${rightEdgeGap.toFixed(1)}px`)
        if (W >= 1100 && rowSpread > 10) issues.push(`desktop composer wrapped unnecessarily: rowSpread=${rowSpread}px`)
      }
    }

    // 3. Desktop thread pane width & title readability.
    let desktopPane = null
    if (currentScreen === 'thread' && W >= 1100) {
      const pane = document.querySelector('.pane')
      const title = document.querySelector('.pane .chattitle .display')
      if (pane && title) {
        const pr = pane.getBoundingClientRect(), tr = title.getBoundingClientRect()
        const truncated = title.scrollWidth > title.clientWidth + 1
        desktopPane = { paneWidth: Math.round(pr.width), titleWidth: Math.round(tr.width), truncated, titleText: title.textContent }
        if (pr.width < 750) issues.push(`desktop pane too narrow: ${pr.width.toFixed(1)}px`)
        if (truncated && tr.width < 220) issues.push(`desktop thread title too cramped: ${tr.width.toFixed(1)}px`)
      }
    }

    const sheet = document.querySelector('[data-menu-surface]:popover-open, .sheet.settings')
    if (sheet) {
      const sr = sheet.getBoundingClientRect()
      if (sr.left < -1 || sr.right > W + 1) issues.push(`sheet horiz out: ${sr.left.toFixed(1)}..${sr.right.toFixed(1)}`)
      if (sr.top < -1 || sr.bottom > H + 1) issues.push(`sheet vert out: ${sr.top.toFixed(1)}..${sr.bottom.toFixed(1)}`)
    }
    for (const b of document.querySelectorAll('.nav button')) {
      const r = b.getBoundingClientRect()
      if (!r.width) continue
      if (r.left < -1 || r.right > W + 1) issues.push(`nav btn out: ${b.textContent.trim()}`)
      if (b.scrollWidth > b.clientWidth + 1) issues.push(`nav btn clip: ${b.textContent.trim()} ${b.scrollWidth}>${b.clientWidth}`)
    }
    const slider = document.querySelector('#appearance-zoom')
    if (slider) {
      const sr = slider.getBoundingClientRect()
      if (sr.width < 60) issues.push(`slider too narrow: ${sr.width.toFixed(1)}px`)
      if (sr.left < -1 || sr.right > W + 1) issues.push(`slider out: ${sr.left.toFixed(1)}..${sr.right.toFixed(1)}`)
    }
    return {
      width: W,
      height: H,
      scale: getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim(),
      app: { x: Math.round(appBox.x), right: Math.round(appBox.right), width: Math.round(appBox.width), height: Math.round(appBox.height) },
      headerRows,
      agentBalance,
      composerInfo,
      desktopPane,
      issues,
    }
  }, screen)
}

const summary = []
try {
  if (baseline) {
    // Baseline mode against main (295ef08): capture the squeezed 200% chat list
    // rows on 390 phone and 1440 desktop with real-length thread titles.
    for (const mode of ['light', 'dark']) {
      values.phone = { mode, size: 200, theme: 'latte' }
      const { ctx: pCtx, page: pPage } = await newPage(390, 844, mode)
      await pPage.goto(app.url)
      await pPage.locator('.tt-row').first().waitFor()
      await pPage.evaluate(() => document.querySelector('.tt-section-heading')?.scrollIntoView({ block: 'start' }))
      await settle(pPage)
      const pm = await measure(pPage, 'chats')
      summary.push({ phase: 'before', width: 390, mode, size: 200, screen: 'chat-rows', ...pm })
      if (out) await pPage.screenshot({ path: path.join(out, `before-rows-390-${mode}-200-chat-rows.png`), animations: 'disabled' })
      await pCtx.close()

      values.desktop = { mode, size: 200, theme: 'latte' }
      const { ctx: dCtx, page: dPage } = await newPage(1440, 900, mode)
      await dPage.goto(app.url)
      await dPage.locator('.tt-row').first().waitFor()
      await settle(dPage)
      const dm = await measure(dPage, 'chats')
      summary.push({ phase: 'before', width: 1440, mode, size: 200, screen: 'chats', ...dm })
      if (out) await dPage.screenshot({ path: path.join(out, `before-rows-1440-${mode}-200-chats.png`), animations: 'disabled' })
      await dCtx.close()
    }
  } else {
    const expectedScale = { 60: '0.54', 100: '0.9', 200: '1.8' }
    for (const [width, height] of [[390, 844], [1440, 900]]) {
      const isPhone = width < 1100
      const kind = isPhone ? 'phone' : 'desktop'
      for (const mode of ['light', 'dark']) {
        for (const size of [60, 100, 200]) {
          values[kind] = { mode, size, theme: 'latte' }
          const { ctx, page } = await newPage(width, height, mode)
          const errors = []
          page.on('pageerror', (e) => errors.push(e.message))

          const check = async (screen) => {
            await settle(page)
            const m = await measure(page, screen)
            assert.equal(m.scale, expectedScale[size], `${width}-${mode}-${size}-${screen} scale`)
            assert.deepEqual(m.issues, [], `${width}-${mode}-${size}-${screen}: ${m.issues.join('; ')}`)
            summary.push({ phase: 'after', width, mode, size, screen, ...m })
            if (out) await page.screenshot({ path: path.join(out, `after-${width}-${mode}-${size}-${screen}.png`), animations: 'disabled' })
          }

          await page.goto(app.url)
          await page.locator('.tt-row').first().waitFor()
          await check('chats')

          // At 200% on phone, also capture the chat rows scrolled clearly into view.
          if (isPhone && size === 200) {
            await page.evaluate(() => document.querySelector('.tt-section-heading')?.scrollIntoView({ block: 'start' }))
            await settle(page)
            if (out) await page.screenshot({ path: path.join(out, `after-390-${mode}-200-chat-rows.png`), animations: 'disabled' })
          }

          // Open the thread containing the long markdown reply + composer.
          await page.locator('.tt-row', { hasText: 'Hosted Loam' }).click()
          await page.locator('.tt-message', { hasText: 'Weekly synthesis and next steps' }).waitFor()
          await page.locator('.tt-message', { hasText: 'Weekly synthesis and next steps' }).scrollIntoViewIfNeeded()
          await check('thread')

          // Open a pop-up sheet (Thread actions).
          await page.locator('.tt-head button[aria-label="Thread actions"]').click()
          await page.locator('[data-menu-surface]:popover-open').waitFor()
          await check('sheet')
          await page.keyboard.press('Escape')
          await settle(page)

          // Files screen.
          if (isPhone) {
            await page.locator('.tt-head button[aria-label="Back"]').first().click()
            await settle(page)
            await page.locator('.nav button', { hasText: 'Files' }).click()
          } else {
            await page.locator('.rail button', { hasText: 'Files' }).click()
          }
          await page.locator('.fs-folder, .fs-entry').first().waitFor()
          await check('files')

          // Settings > Appearance (SIZE card visible).
          if (isPhone) {
            await page.locator('.nav button[aria-label="Settings"]').click()
            await page.getByText('Appearance', { exact: true }).click()
          } else {
            await page.locator('.rail button[aria-label="Settings"]').click()
            await page.locator('.setnav').getByText('Appearance', { exact: true }).click()
          }
          await page.locator('#appearance-size').scrollIntoViewIfNeeded()
          await page.locator('.appearance-reset').scrollIntoViewIfNeeded()
          await check('settings')

          assert.deepEqual(errors, [], `${width}-${mode}-${size} page errors`)
          console.log(`PASS ${width} ${mode} ${size}% (--ui-scale: ${expectedScale[size]})`)
          await ctx.close()
        }
      }
    }

    // Verify older-host refusal fallback for 60% and 200%, reload persistence,
    // and automatic migration once the host accepts 60..200.
    values.phone = { mode: 'light', size: 100, theme: 'latte' }
    refuseExtended = true
    const { ctx, page } = await newPage(390, 844, 'light')
    await page.goto(app.url)
    await page.locator('.nav button[aria-label="Settings"]').click()
    await page.getByText('Appearance', { exact: true }).click()
    await page.locator('#appearance-zoom').evaluate((el) => {
      el.value = '60'
      el.dispatchEvent(new Event('input', { bubbles: true }))
      el.dispatchEvent(new Event('change', { bubbles: true }))
    })
    await page.waitForFunction(() => document.querySelector('.appearance-status')?.textContent.includes('Size saved on this device'))
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim()), '0.54')
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('loam.appearance.size')).phone), 60)

    await page.reload()
    await page.locator('.tt-row').first().waitFor()
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-scale').trim()), '0.54')

    refuseExtended = false
    await page.reload()
    await page.waitForFunction(() => localStorage.getItem('loam.appearance.size') === null)
    assert.equal(values.phone.size, 60)
    await ctx.close()
    console.log('PASS older-host extended-size fallback and migration')
  }

  if (out) {
    await writeFile(path.join(out, baseline ? 'baseline-rows.json' : 'after.json'), JSON.stringify({ fixtureOnly: true, summary }, null, 2) + '\n')
  }
} finally {
  await browser.close()
  await app.close()
}
