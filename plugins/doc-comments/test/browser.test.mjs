// The editor in a real browser, against the dev server (test/dev_server.py,
// the real service over an in-memory store): what is placed beside a line
// sits beside it, and typing does what it says, on disk and on the screen.
//
// It needs Playwright and a Chromium, which Loam does not ship; without them
// every test here is skipped. Run:
//   LOAM_PLAYWRIGHT=/path/to/node_modules/playwright/index.mjs \
//   [LOAM_CHROME=/path/to/chrome-headless-shell] \
//   node --test plugins/doc-comments/test/browser.test.mjs
// `DC_SHOTS=<dir>` keeps a screenshot of each place it measures.
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PW = process.env.LOAM_PLAYWRIGHT
const skip = PW ? false : 'LOAM_PLAYWRIGHT is not set (a Playwright install to drive Chromium)'
const SHOTS = process.env.DC_SHOTS || ''
const TOL = 4

let chromium = null
let server = null, base = '', browser = null

async function start() {
  if (browser) return
  ;({ chromium } = await import(PW))
  server = spawn('python3', [path.join(HERE, 'dev_server.py'), '0'], { stdio: ['ignore', 'pipe', process.env.DC_VERBOSE ? 'inherit' : 'ignore'] })
  base = await new Promise((resolve, reject) => {
    let buf = ''
    server.stdout.on('data', (d) => {
      buf += d
      const m = /doc-comments dev: (http:\/\/127\.0\.0\.1:\d+)\//.exec(buf)
      if (m) resolve(m[1])
    })
    server.on('exit', (code) => reject(new Error(`dev server exited ${code}`)))
  })
  browser = await chromium.launch(process.env.LOAM_CHROME ? { executablePath: process.env.LOAM_CHROME } : {})
}

test.after(async () => {
  await browser?.close()
  server?.kill()
})

let serial = 0
async function newDoc(text) {
  const p = `Tests/t-${process.pid}-${++serial}.md`
  await fetch(`${base}/dev/outside`, { method: 'POST', body: JSON.stringify({ path: p, text }) })
  return p
}
const disk = async (p) => (await (await fetch(`${base}/dev/file?path=${encodeURIComponent(p)}`)).json())
const outside = (p, text) => fetch(`${base}/dev/outside`, { method: 'POST', body: JSON.stringify({ path: p, text }) })

async function open({ doc = 'Essays/drafts/Long.md', w = 1280, h = 850, zoom = 1, phone = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(`${base}/?dark&zoom=${zoom}${phone ? '&phone' : ''}&path=${encodeURIComponent(doc)}`)
  await page.waitForSelector('.cm-content')
  await page.waitForTimeout(500)
  page.errors = errors
  page.done = () => ctx.close()
  return page
}

const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${name}.png`) }) }
const until = async (page, fn, arg, ms = 6000) => page.waitForFunction(fn, arg, { timeout: ms, polling: 100 })

// Every note whose passage is drawn: the top of its card's head row against
// the top of its passage's first line (desktop), or its dot against the top
// of the line it marks (phone).
async function offsets(page, phone) {
  return page.evaluate((phone) => {
    const out = []
    if (!phone) {
      for (const card of document.querySelectorAll('.dc-margin .dc-card')) {
        const span = document.querySelector(`.cm-content [data-note="${card.dataset.id}"]`)
        if (!span) continue
        // the first character's own box, not the mark's (which has padding)
        const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT)
        const node = walker.nextNode()
        const range = document.createRange()
        range.setStart(node, 0); range.setEnd(node, Math.min(1, node.length))
        out.push({ id: card.dataset.id, d: card.querySelector('.dc-ch').getBoundingClientRect().top - range.getClientRects()[0].top })
      }
    } else {
      const ed = window.__ed
      for (const n of ed.state.notes.filter(n => n.state === 'open' && n.from != null)) {
        const span = document.querySelector(`.cm-content [data-note="${n.id}"]`)
        if (!span) continue
        const line = span.closest('.cm-line').getBoundingClientRect().top
        const marks = [...document.querySelectorAll('.dc-mk')].map(m => m.getBoundingClientRect().top - parseFloat(getComputedStyle(m).marginTop) * (window.__ed.dom.getBoundingClientRect().width / window.__ed.dom.offsetWidth))
        const best = marks.reduce((a, t) => (Math.abs(t - line) < Math.abs(a - line) ? t : a), Infinity)
        out.push({ id: n.id, d: best - line })
      }
    }
    return out
  }, phone)
}

function near(list, label) {
  assert.ok(list.length > 0, `${label}: some note is drawn`)
  for (const o of list) assert.ok(Math.abs(o.d) <= TOL, `${label}: note ${o.id} is ${o.d.toFixed(1)} px from its line`)
}

async function scrollTo(page, y) {
  await page.evaluate((y) => { document.querySelector('.dc-scroll').scrollTop = y }, y)
  await page.waitForTimeout(450)
}

for (const zoom of [1, 0.9]) {
  for (const [w, h, phone] of [[1280, 850, false], [430, 900, true]]) {
    test(`notes sit beside their lines: ${w}x${h} at ${zoom * 100}%`, { skip }, async () => {
      await start()
      const page = await open({ w, h, zoom, phone })
      const tag = `pos-${w}-${zoom}`
      const original = (await disk('Essays/drafts/Long.md')).text
      await page.waitForTimeout(400)
      near(await offsets(page, phone), 'after load')
      await shot(page, `${tag}-top`)
      let seen = 0
      for (const y of [1200, 2600, 4000, 6000, 99999]) {
        await scrollTo(page, y)
        const o = await offsets(page, phone)
        seen += o.length
        if (o.length) near(o, `after scrolling to ${y}`)
      }
      assert.ok(seen >= 4, `notes further down were drawn and measured (${seen})`)
      await shot(page, `${tag}-bottom`)
      // A resize, then an edit above: every drawn note follows.
      await page.setViewportSize({ width: w + (phone ? 20 : -180), height: h - 60 })
      await page.waitForTimeout(500)
      const afterResize = await offsets(page, phone)
      if (afterResize.length) near(afterResize, 'after a resize')
      const added = '\n\nA new paragraph above everything, long enough to wrap onto a second line of the column and push the notes down.'
      await page.evaluate((added) => {
        const v = window.__ed.view
        const at = v.state.doc.toString().indexOf('# A long doc') + '# A long doc'.length
        v.dispatch({ changes: { from: at, insert: added } })
      }, added)
      await page.waitForTimeout(500)
      const afterEdit = await offsets(page, phone)
      if (afterEdit.length) near(afterEdit, 'after an edit above')
      let later = []
      for (const y of [1200, 2600, 4000]) { await scrollTo(page, y); later = later.concat(await offsets(page, phone)) }
      near(later, 'after the edit, further down')
      await shot(page, `${tag}-middle`)
      // Put the doc back as it was for the next test.
      await page.evaluate((added) => {
        const v = window.__ed.view
        const at = v.state.doc.toString().indexOf(added)
        v.dispatch({ changes: { from: at, to: at + added.length } })
      }, added)
      await page.evaluate(() => window.__ed.flush())
      const unstamped = (t) => t.replace(/\nlast_modified_by:[^\n]*/, '')
      assert.equal(unstamped((await disk('Essays/drafts/Long.md')).text), unstamped(original), 'the doc is back as it was')
      assert.deepEqual(page.errors, [])
      await page.done()
    })
  }
}

test('the selection popup, the link field and the "+" sit at their lines (90%)', { skip }, async () => {
  await start()
  const p = await newDoc('# Popups\n\n' + Array.from({ length: 30 }, (_, i) => `Paragraph ${i} with some words to select in it.`).join('\n\n') + '\n\n\n')
  const page = await open({ doc: p, zoom: 0.9 })
  await scrollTo(page, 900)
  const sel = await page.evaluate(() => {
    const v = window.__ed.view
    const t = v.state.doc.toString()
    const from = t.indexOf('Paragraph 20') , to = from + 'Paragraph 20'.length
    v.focus()
    v.dispatch({ selection: { anchor: from, head: to }, scrollIntoView: true })
    return { from, to }
  })
  await page.mouse.move(400, 400)
  await page.evaluate(() => window.__ed.view.contentDOM.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })))
  await page.waitForTimeout(300)
  const gap = await page.evaluate(({ from }) => {
    const bar = document.querySelector('.dc-selbar.on').getBoundingClientRect()
    const c = window.__ed.view.coordsAtPos(from, 1)
    return c.top - bar.bottom
  }, sel)
  assert.ok(gap >= 0 && gap <= 16, `the popup sits just above the selection (${gap.toFixed(1)} px)`)
  await shot(page, 'selbar-90')
  await page.keyboard.press('Control+k')
  await page.waitForTimeout(200)
  const lgap = await page.evaluate(({ from }) => document.querySelector('.dc-selbar.on') && (window.__ed.view.coordsAtPos(from, 1).top - document.querySelector('.dc-selbar.on').getBoundingClientRect().bottom), sel)
  assert.ok(lgap >= 0 && lgap <= 16, `the link field sits just above the selection (${lgap})`)
  await page.keyboard.press('Escape')
  // The "+" on an empty line near the end.
  const plus = await page.evaluate(() => {
    const v = window.__ed.view
    v.focus()
    v.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true })
    return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => {
      const b = document.querySelector('.dc-plus.on')?.getBoundingClientRect()
      const line = v.coordsAtPos(v.state.doc.length)
      const lineEl = v.domAtPos(v.state.doc.length).node
      const el = (lineEl.nodeType === 1 ? lineEl : lineEl.parentElement).closest('.cm-line').getBoundingClientRect()
      r(b ? (b.top + b.bottom) / 2 - (el.top + el.bottom) / 2 : null)
    })))
  })
  assert.ok(plus !== null && Math.abs(plus) <= TOL, `the "+" is centred on its empty line (${plus})`)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('the block menu opens below the caret with every row showing (850 px)', { skip }, async () => {
  await start()
  const p = await newDoc('# Menu\n\nFirst paragraph.\n\n')
  const page = await open({ doc: p })
  await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length } }) })
  await page.keyboard.type('/')
  await page.waitForSelector('.dc-bmenu.on')
  const m = await page.evaluate(() => {
    const menu = document.querySelector('.dc-bmenu.on')
    const r = menu.getBoundingClientRect()
    const c = window.__ed.view.coordsAtPos(window.__ed.view.state.selection.main.head)
    return { below: r.top >= c.bottom - 1, scrolls: menu.scrollHeight > menu.clientHeight + 1, rows: menu.querySelectorAll('.dc-bi').length, bottom: r.bottom, h: innerHeight }
  })
  assert.equal(m.below, true, 'below the caret')
  assert.equal(m.scrolls, false, 'no inner scrolling')
  assert.equal(m.rows, 11)
  assert.ok(m.bottom <= m.h)
  await shot(page, 'block-menu')
  await page.keyboard.press('Escape')
  // From a selection, Aa turns the text into something: no rows that add a block.
  await page.evaluate(() => { const v = window.__ed.view; const t = v.state.doc.toString(); const a = t.indexOf('First'); v.dispatch({ selection: { anchor: a, head: a + 5 } }); v.contentDOM.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })) })
  await page.waitForSelector('.dc-selbar.on .dc-fb.aa')
  await page.click('.dc-selbar.on .dc-fb.aa')
  const labels = await page.$$eval('.dc-bmenu.on .dc-bl', els => els.map(e => e.textContent))
  assert.ok(!labels.includes('Code block') && !labels.includes('Table') && !labels.includes('Divider'), labels.join(', '))
  await page.done()
})

test('the block menu, opened halfway down at 90%, makes room below the caret rather than scroll inside', { skip }, async () => {
  await start()
  const p = await newDoc('# Middle\n\n' + Array.from({ length: 30 }, (_, i) => `Paragraph ${i} of a doc long enough to scroll.`).join('\n\n') + '\n')
  const page = await open({ doc: p, zoom: 0.9 })
  await page.evaluate(() => {
    const v = window.__ed.view
    const at = v.state.doc.toString().indexOf('Paragraph 12') - 1
    v.focus()
    v.dispatch({ selection: { anchor: at } })
    const y = v.coordsAtPos(at).top - document.querySelector('.dc-scroll').getBoundingClientRect().top
    document.querySelector('.dc-scroll').scrollTop += y - 380
  })
  await page.waitForTimeout(200)
  await page.keyboard.press('Enter')
  await page.keyboard.type('/')
  await page.waitForSelector('.dc-bmenu.on')
  await page.waitForTimeout(300)
  const m = await page.evaluate(() => {
    const menu = document.querySelector('.dc-bmenu.on')
    const r = menu.getBoundingClientRect()
    const c = window.__ed.view.coordsAtPos(window.__ed.view.state.selection.main.head)
    const head = document.querySelector('.dc-head').getBoundingClientRect()
    return { below: r.top >= c.bottom - 1, scrolls: menu.scrollHeight > menu.clientHeight + 1, bottom: r.bottom, h: innerHeight, caretVisible: c.top >= head.bottom }
  })
  assert.deepEqual({ below: m.below, scrolls: m.scrolls, caretVisible: m.caretVisible }, { below: true, scrolls: false, caretVisible: true })
  assert.ok(m.bottom <= m.h)
  await shot(page, 'block-menu-middle-90')
  await page.done()
})

async function mode(page, label) {
  await page.click('.dc-mpill')
  await page.click(`.dc-menu .dc-mi:has-text("${label}")`)
  await page.waitForTimeout(150)
}

async function caretAfter(page, words) {
  await page.evaluate((words) => {
    const v = window.__ed.view
    const at = v.state.doc.toString().indexOf(words)
    if (at < 0) throw new Error(`no ${words}`)
    v.focus()
    v.dispatch({ selection: { anchor: at + words.length } })
  }, words)
}

const openSuggestions = (page) => page.evaluate(() => window.__ed.state.notes.filter(n => n.kind === 'suggestion' && n.state === 'open').map(n => ({ id: n.id, quote: n.anchor.quote, replacement: n.replacement })))

test('Suggesting: type, accept, type again; the accepted text is the base everywhere', { skip }, async () => {
  await start()
  const text = '# Roots\n\nThe first winter I stayed, nothing happened, and that was the point. The baker learned my order.\n\nI think this is what roots are: not comfort, but accountability.\n'
  const p = await newDoc(text)
  const page = await open({ doc: p })
  const toasts = []
  await page.exposeFunction('__toast', (t) => toasts.push(t))
  await page.evaluate(() => new MutationObserver(() => { const t = document.querySelector('.dc-toast.on'); if (t) window.__toast(t.textContent) }).observe(document.querySelector('.dc-toast'), { childList: true, subtree: true, attributes: true }))
  await mode(page, 'Suggesting')
  await caretAfter(page, 'that was the point.')
  await page.keyboard.type(' Zed.')
  await until(page, () => window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  let open1 = await openSuggestions(page)
  assert.equal(open1.length, 1)
  await page.click(`.dc-card[data-id="${open1[0].id}"]`)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, () => !window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  let d = await disk(p)
  assert.ok(d.text.includes('that was the point. Zed. The baker'), d.text)
  // Type again, elsewhere.
  await caretAfter(page, 'roots are:')
  await page.keyboard.type(' Yak.')
  await until(page, () => window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  await page.waitForTimeout(400)
  const editorText = await page.evaluate(() => window.__ed.text())
  d = await disk(p)
  assert.equal(editorText, d.text, 'the editor holds the stored text (the accept stands)')
  const open2 = await openSuggestions(page)
  assert.equal(open2.length, 1, `one open suggestion, not a copy of the accepted one: ${JSON.stringify(open2)}`)
  // Anchored on the word before it (a passage is never empty), where it was typed.
  assert.equal(open2[0].quote.trim(), 'are:')
  assert.equal(open2[0].replacement.replace(/\s+/g, ' ').trim(), 'are: Yak.')
  const side = d.sidecar.threads.filter(t => t.state === 'open')
  assert.equal(side.length, 1)
  assert.equal(side[0].anchor.quote.trim(), 'are:')
  assert.equal(d.text.slice(side[0].where.start, side[0].where.end).trim(), 'are:')
  // Drawn as an insertion: nothing struck, only the new words.
  const drawn = await page.evaluate((id) => ({ del: document.querySelectorAll(`.dc-sg-del[data-note="${id}"]`).length, ins: document.querySelector(`.dc-sg-ins[data-note="${id}"]`)?.textContent }), open2[0].id)
  assert.deepEqual({ ...drawn, ins: drawn.ins?.trim() }, { del: 0, ins: 'Yak.' })
  await page.click(`.dc-card[data-id="${open2[0].id}"]`)
  assert.match(await page.textContent('.dc-card.on .dc-stl'), /^Add “Yak\.”/)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, () => !window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  d = await disk(p)
  assert.ok(d.text.includes('what roots are: Yak. not comfort'), d.text)
  assert.equal(d.text.split('Zed.').length, 2, 'Zed. once')
  assert.equal(await page.evaluate(() => window.__ed.text()), d.text)
  // Accept's Undo puts the words back and the suggestion with them.
  assert.ok(toasts.some(t => /Accepted/.test(t) && /Undo/.test(t)), `Accept's toast has Undo: ${toasts}`)
  await page.click('.dc-toast.on button')
  await until(page, () => window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  d = await disk(p)
  assert.ok(d.text.includes('what roots are: not comfort'), d.text)
  assert.ok(!toasts.some(t => /Mismatched|change set|TypeError|RangeError/i.test(t)), `no library error in a toast: ${toasts}`)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Suggesting: type, delete a word, accept one, reject the other: no phantom copy, even after a reload', { skip }, async () => {
  await start()
  const p = await newDoc('# Two\n\nThe baker learned my order. The man with the grey dog started nodding.\n')
  let page = await open({ doc: p })
  await mode(page, 'Suggesting')
  await caretAfter(page, 'learned my order.')
  await page.keyboard.type(' She smiled.')
  await page.waitForTimeout(2300)
  await caretAfter(page, 'the grey')
  for (let i = 0; i < 5; i++) await page.keyboard.press('Backspace')
  await until(page, () => window.__ed.state.notes.filter(n => n.kind === 'suggestion' && n.state === 'open').length === 2)
  const both = await openSuggestions(page)
  const ins = both.find(s => s.replacement.includes('smiled'))
  const del = both.find(s => s !== ins)
  // The insertion is drawn and described as one.
  const drawn = await page.evaluate((id) => ({ del: document.querySelectorAll(`.dc-sg-del[data-note="${id}"]`).length, ins: document.querySelector(`.dc-sg-ins[data-note="${id}"]`)?.textContent }), ins.id)
  assert.deepEqual({ ...drawn, ins: drawn.ins?.trim() }, { del: 0, ins: 'She smiled.' })
  await shot(page, 'pure-insertion')
  await page.click(`.dc-card[data-id="${ins.id}"]`)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.state === 'accepted', ins.id)
  await page.click(`.dc-card[data-id="${del.id}"]`)
  await page.click('.dc-card.on .dc-sm:has-text("Reject")')
  await until(page, () => !window.__ed.state.notes.some(n => n.kind === 'suggestion' && n.state === 'open'))
  await page.waitForTimeout(2500)
  assert.deepEqual(await openSuggestions(page), [])
  await page.done()
  page = await open({ doc: p })
  assert.deepEqual(await openSuggestions(page), [], 'none after a reload either')
  const d = await disk(p)
  assert.ok(d.text.includes('my order. She smiled. The man with the grey dog'), d.text)
  assert.equal(await page.evaluate(() => window.__ed.text()), d.text)
  await page.done()
})

test('an open doc takes in a change on disk within two seconds, keeping the caret and the scroll', { skip }, async () => {
  await start()
  const paras = Array.from({ length: 40 }, (_, i) => `Paragraph ${i}, which is long enough to be a real line of text in the column.`)
  const text = '# Outside\n\n' + paras.join('\n\n') + '\n'
  const p = await newDoc(text)
  const page = await open({ doc: p })
  await scrollTo(page, 1200)
  await caretAfter(page, 'Paragraph 30,')
  const before = await page.evaluate(() => {
    const v = window.__ed.view
    const at = v.state.doc.toString().indexOf('Paragraph 25')
    return { y: v.coordsAtPos(at).top, scroll: document.querySelector('.dc-scroll').scrollTop }
  })
  await outside(p, text.replace('# Outside\n\n', '# Outside\n\nA paragraph added on disk, above everything.\n\n').replace('Paragraph 35,', 'Paragraph 35 (changed on disk),'))
  await until(page, () => window.__ed.text().includes('changed on disk'), null, 2500)
  await page.waitForTimeout(300)
  const after = await page.evaluate(() => {
    const v = window.__ed.view
    const t = v.state.doc.toString()
    const at = t.indexOf('Paragraph 25')
    return { y: v.coordsAtPos(at).top, caretAfter: t.slice(v.state.selection.main.head - 13, v.state.selection.main.head) }
  })
  assert.ok(Math.abs(after.y - before.y) <= 2, `what was being read did not move (${before.y} → ${after.y})`)
  assert.equal(after.caretAfter, 'Paragraph 30,')
  // With an unsaved edit: both land.
  await page.keyboard.type(' typed here')
  const d0 = await disk(p)
  await outside(p, d0.text.replace('Paragraph 2,', 'Paragraph 2 (disk again),'))
  await until(page, () => window.__ed.text().includes('disk again'), null, 4000)
  await page.waitForTimeout(1600)
  const d = await disk(p)
  assert.ok(d.text.includes('disk again') && d.text.includes('Paragraph 30, typed here'), d.text.slice(0, 400))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('typing: Enter makes paragraphs, lists and quotes end with a blank line, "[] " and ``` convert', { skip }, async () => {
  await start()
  const p = await newDoc('# Typing\n\n')
  const page = await open({ doc: p })
  await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length } }) })
  const k = page.keyboard
  await k.type('First paragraph.')
  await k.press('Enter')
  await k.type('Second paragraph.')
  await k.press('Shift+Enter')
  await k.type('Same paragraph, next line.')
  await k.press('Enter')
  await k.type('- one')
  await k.press('Enter')
  await k.type('two')
  await k.press('Enter')
  await k.press('Enter')
  await k.type('After the list.')
  await k.press('Enter')
  await k.type('> quoted')
  await k.press('Enter')
  await k.press('Enter')
  await k.type('After the quote.')
  await k.press('Enter')
  await k.type('[] a task')
  await k.press('Enter')
  await k.press('Enter')
  await k.type('```')
  const fenced = await page.evaluate(() => window.__ed.text())
  assert.ok(fenced.endsWith('```\n```'), JSON.stringify(fenced.slice(-20)))
  await page.keyboard.press('Control+s')
  await page.waitForTimeout(800)
  const d = await disk(p)
  const want = '# Typing\n\nFirst paragraph.\n\nSecond paragraph.\nSame paragraph, next line.\n\n- one\n- two\n\nAfter the list.\n\n> quoted\n\nAfter the quote.\n\n- [ ] a task\n\n```\n```'
  // (the store stamps who saved it in the frontmatter, as the broker does)
  assert.equal(d.text.replace(/^---\n[\s\S]*?\n---\n\n?/, ''), want)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('every doc opens in Editing, whatever was used last', { skip }, async () => {
  await start()
  const p = await newDoc('# Mode\n\nWords.\n')
  let page = await open({ doc: p })
  await mode(page, 'Suggesting')
  await page.done()
  page = await open({ doc: 'Essays/drafts/Fresh.md' })
  assert.equal(await page.evaluate(() => window.__ed.state.mode), 'edit')
  // and a doc with no notes centres its column at the desktop width
  const centred = await page.evaluate(() => {
    const c = document.querySelector('.cm-content').getBoundingClientRect(), r = document.querySelector('.dc').getBoundingClientRect()
    return Math.abs((c.left - r.left) - (r.right - c.right))
  })
  assert.ok(centred < 90, `the column is near the middle (${centred})`)
  await page.done()
})

test('phone: the thread sheet leaves its passage in view above it', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/Long.md', w: 430, h: 900, phone: true })
  await scrollTo(page, 99999)
  await page.waitForTimeout(300)
  const id = await page.evaluate(() => window.__ed.state.notes.filter(n => n.state === 'open').sort((a, b) => b.from - a.from)[0].id)
  await page.evaluate((id) => {
    const n = window.__ed.state.notes.find(x => x.id === id)
    const v = window.__ed.view
    const top = v.lineBlockAt(n.from).top
    const marks = [...document.querySelectorAll('.dc-mk')]
    const m = marks.reduce((a, x) => (Math.abs(parseFloat(x.style.top) - top) < Math.abs(parseFloat(a.style.top) - top) ? x : a))
    m.click()
  }, id)
  await page.waitForTimeout(900)
  const r = await page.evaluate((id) => {
    const span = document.querySelector(`.cm-content [data-note="${id}"]`)
    const s = span.getClientRects()[0]
    return { passage: s.bottom, sheet: document.querySelector('.dc-sheet.on').getBoundingClientRect().top, top: s.top, scrollTop: document.querySelector('.dc-scroll').getBoundingClientRect().top }
  }, id)
  assert.ok(r.passage <= r.sheet - 8 && r.top >= r.scrollTop, `the passage (${r.top}–${r.passage}) is above the sheet (${r.sheet})`)
  await shot(page, 'phone-sheet')
  await page.done()
})
