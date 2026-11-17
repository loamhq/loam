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
const shareLinks = {}

async function start() {
  if (browser) return
  ;({ chromium } = await import(PW))
  server = spawn('python3', [path.join(HERE, 'dev_server.py'), '0'], { stdio: ['ignore', 'pipe', process.env.DC_VERBOSE ? 'inherit' : 'ignore'] })
  base = await new Promise((resolve, reject) => {
    let buf = ''
    server.stdout.on('data', (d) => {
      buf += d
      const m = /doc-comments dev: (http:\/\/127\.0\.0\.1:\d+)\//.exec(buf)
      for (const l of buf.matchAll(/(\w+) link: http:\/\/127\.0\.0\.1:\d+(\/s\/\S+)\s+password: (\S+)/g)) shareLinks[l[1]] = { path: l[2], password: l[3] }
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
      // the dot's centre against the centre of the passage's first character
      for (const n of window.__ed.state.notes.filter(n => n.state === 'open' && n.from != null)) {
        const span = document.querySelector(`.cm-content [data-note="${n.id}"]`)
        if (!span) continue
        const node = document.createTreeWalker(span, NodeFilter.SHOW_TEXT).nextNode()
        const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, Math.min(1, node.length))
        const g = range.getClientRects()[0]
        const mid = (g.top + g.bottom) / 2
        const marks = [...document.querySelectorAll('.dc-mk')].map(m => { const r = m.getBoundingClientRect(); return (r.top + r.bottom) / 2 })
        const best = marks.reduce((a, t) => (Math.abs(t - mid) < Math.abs(a - mid) ? t : a), Infinity)
        out.push({ id: n.id, d: best - mid })
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
      const atLoad = await offsets(page, phone)
      if (atLoad.length) near(atLoad, 'after load')
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

// -- Suggesting --------------------------------------------------------------
//
// What must hold (the staging walk, 2026-11-16): while I am Suggesting, my own
// run of typing and deleting in one place stays one suggestion that keeps
// growing, the caret moves only by my keys, accepting writes exactly what I
// saw, and rejecting puts back the original byte for byte.

const body = (t) => t.replace(/^---\n[\s\S]*?\n---\n/, (fm) => {
  const keep = fm.split('\n').filter(l => !/^(loam_doc_id|last_modified_by):/.test(l))
  return keep.length <= 3 ? '' : keep.join('\n')
})
const api = (p, route, method = 'POST', extra = {}, agent = null) => fetch(`${base}/x/doc-comments/${route}${method === 'GET' ? `?${new URLSearchParams({ root: 'vault', path: p })}` : ''}`, {
  method, headers: { 'Content-Type': 'application/json', ...(agent ? { 'X-Loam-Agent': agent } : {}) },
  body: method === 'GET' ? undefined : JSON.stringify({ root: 'vault', path: p, ...extra }),
}).then(r => r.json())
const mineOpen = async (p) => (await api(p, 'doc', 'GET')).notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author.id === 'sam')
const shown = (page) => page.evaluate(() => window.__ed.text())
// Where the caret is, as the text on either side of it: a line added above
// (a doc's first note adds one to its frontmatter) moves its number, not it.
const caretPos = (page) => page.evaluate(() => { const v = window.__ed.view, h = v.state.selection.main.head; return v.state.sliceDoc(Math.max(0, h - 12), h) + '|' + v.state.sliceDoc(h, h + 12) })
// The same caret: the same text after it, and the shorter text before it
// ending the longer (near the top, a line added above shows in the window).
const sameCaret = (a, b) => {
  const [ab, aa] = a.split('|'), [bb, ba] = b.split('|')
  return aa === ba && (ab.endsWith(bb) || bb.endsWith(ab))
}
const PAUSE = 2300            // longer than the pause after which typing is sent

async function settle(page) {
  await page.evaluate(() => window.__ed.flush())
  await page.waitForTimeout(100)
}

// Accept (or reject) every suggestion of mine, as the server's own caller.
async function closeMine(p, how) {
  for (const n of await mineOpen(p)) await api(p, `doc/notes/${n.id}/${how}`)
}

async function suggestDoc(text) {
  const p = await newDoc(text)
  const page = await open({ doc: p })
  await mode(page, 'Suggesting')
  return { p, page }
}

const TEXT = '# Roots\n\nThe first winter I stayed, nothing happened, and that was the point. The baker learned my order. The man with the grey dog started nodding.\n\nI think this is what roots are: not comfort, but accountability.\n'

test('Suggesting: the walk\'s two repros, with real pauses', { skip }, async () => {
  await start()
  // Repro 1: type, pause, type, accept.
  let { p, page } = await suggestDoc(TEXT)
  await caretAfter(page, 'learned my order.')
  await page.keyboard.type(' Aaa.')
  await page.waitForTimeout(PAUSE)
  let c = await caretPos(page)
  assert.ok(c.split('|')[0].endsWith('order. Aaa.') && c.split('|')[1].startsWith(' The man'), `the pause left the caret where it was: ${c}`)
  await page.keyboard.type(' Bbb.')
  c = await caretPos(page)
  assert.ok(c.split('|')[0].endsWith('Aaa. Bbb.') && c.split('|')[1].startsWith(' The man'), `the caret moved only by the keys: ${c}`)
  await page.waitForTimeout(PAUSE)
  assert.equal((await mineOpen(p)).length, 1, 'one suggestion, grown')
  const seen = await shown(page)
  assert.ok(seen.includes('my order. Aaa. Bbb. The man'), seen)
  await shot(page, 'suggest-bursts')
  await closeMine(p, 'accept')
  assert.equal(body((await disk(p)).text), body(seen))
  await page.done()
  // Repro 2: type, pause, type, pause, Backspace.
  ;({ p, page } = await suggestDoc(TEXT))
  await caretAfter(page, 'nothing happened,')
  await page.keyboard.type(' aa')
  await page.waitForTimeout(PAUSE)
  await page.keyboard.type(' bb')
  await page.waitForTimeout(PAUSE)
  await page.keyboard.press('Backspace')
  await page.waitForTimeout(PAUSE)
  const two = await shown(page)
  assert.ok(two.includes('nothing happened, aa b and that'), two)
  const open2 = await mineOpen(p)
  assert.equal(open2.length, 1, JSON.stringify(open2.map(n => [n.anchor.quote, n.replacement])))
  await closeMine(p, 'reject')
  assert.equal(body((await disk(p)).text), body(TEXT), 'reject restores the original')
  await page.waitForTimeout(2200)
  assert.equal(body(await shown(page)), body(TEXT), 'and the editor shows it')
  assert.deepEqual(page.errors, [])
  await page.done()
})

// Each case: where the caret goes, then bursts (typed text, or {bs: n}),
// each followed by a pause longer than the autosave.
const CASES = [
  ['at the start of a word', 'The baker', -5, ['Old ', 'er ']],
  ['in the middle of a word', 'The ba', 0, ['xx', 'yy']],
  ['at the end of a word', 'The baker', 0, ['s', ' too']],
  ['before punctuation', 'my order', 0, [' please', ' now']],
  ["at a paragraph's end", 'started nodding.', 0, [' Then', ' he left.']],
  ['with Backspace across what was typed', 'the grey', 0, [' old', { bs: 8 }, ' white']],
  ['at the very start of the text', '', 0, ['Note: ', 'read me. ']],
]

for (const [name, after, shift, bursts] of CASES) {
  for (const how of ['accept', 'reject']) {
    test(`Suggesting in bursts ${name}, then ${how}`, { skip }, async () => {
      await start()
      const { p, page } = await suggestDoc(TEXT)
      if (after) await caretAfter(page, after)
      else await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: 0 } }) })
      if (shift) for (let i = 0; i < -shift; i++) await page.keyboard.press('ArrowLeft')
      for (const b of bursts) {
        if (typeof b === 'string') await page.keyboard.type(b)
        else for (let i = 0; i < b.bs; i++) await page.keyboard.press('Backspace')
        const typed = await caretPos(page)
        if (typeof b === 'string') assert.ok(typed.split('|')[0].endsWith(b.slice(-12)), `the caret is after what was typed: ${typed}`)
        await page.waitForTimeout(PAUSE)
        assert.ok(sameCaret(await caretPos(page), typed), `the pause did not move the caret: ${typed} then ${await caretPos(page)}`)
      }
      const seen = await shown(page)
      assert.ok((await mineOpen(p)).length <= 1, 'one run in one place is one suggestion')
      await closeMine(p, how)
      const d = body((await disk(p)).text)
      assert.equal(d, how === 'accept' ? body(seen) : body(TEXT))
      await page.waitForTimeout(2000)
      assert.equal(body(await shown(page)), d, 'the editor shows what is stored')
      assert.deepEqual(page.errors, [])
      await page.done()
    })
  }
}

test('Suggesting next to and inside someone else\'s suggestion, and with a change on disk mid-pause', { skip }, async () => {
  await start()
  const p = await newDoc(TEXT)
  await api(p, 'doc/notes', 'POST', { kind: 'suggestion', quote: 'the grey dog', replacement: 'the black dog', text: '' }, 'ada')
  const page = await open({ doc: p })
  await mode(page, 'Suggesting')
  await caretAfter(page, 'the grey dog')
  await page.keyboard.type(' too')
  await page.waitForTimeout(PAUSE)
  await caretAfter(page, 'the gr')
  await page.keyboard.type('ee')
  await page.waitForTimeout(PAUSE)
  // someone saves elsewhere in the doc while I pause
  const d0 = (await disk(p)).text
  await outside(p, d0.replace('accountability', 'accountability, slowly'))
  await page.waitForTimeout(PAUSE)
  await caretAfter(page, 'not comfort')
  await page.keyboard.type(' at all')
  await page.waitForTimeout(PAUSE)
  const seen = await shown(page)
  assert.ok(seen.includes('slowly') && seen.includes('greeey dog too') && seen.includes('comfort at all'), seen)
  await closeMine(p, 'accept')
  assert.equal(body((await disk(p)).text), body(seen))
  // Ada's is still there, open, on what is left of its words or changed
  const ada = (await api(p, 'doc', 'GET')).notes.filter(n => n.author.id === 'ada' && n.state === 'open')
  assert.equal(ada.length, 1)
  assert.deepEqual(page.errors, [])
  await page.done()
})

// -- Suggesting with several drafts (round 3) ----------------------------------

// The stored text with every open suggestion of mine applied: what the
// editor must show while Suggesting.
async function storedWithMine(p) {
  const d = await api(p, 'doc', 'GET')
  const mine = d.notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author.id === 'sam' && n.where?.state === 'attached').sort((a, b) => a.where.start - b.where.start)
  let out = '', at = 0
  for (const n of mine) { out += d.text.slice(at, n.where.start) + n.replacement; at = n.where.end }
  return { text: out + d.text.slice(at), mine, stored: d.text }
}
async function closeOne(page, p, id, how) {
  await api(p, `doc/notes/${id}/${how}`)
  await page.evaluate(() => window.__ed.reload())
  await page.waitForTimeout(150)
}
async function selectText(page, from, to) {
  await page.evaluate(([a, b]) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a, head: b } }) }, [from, to])
}
const indexIn = (page, words, after = true) => page.evaluate(([w, af]) => { const t = window.__ed.text(); const i = t.indexOf(w); return i < 0 ? -1 : i + (af ? w.length : 0) }, [words, after])
const LONGTEXT = '# Walk\n\n' + [1, 2, 3, 4, 5, 6].map(i => `Paragraph ${i}a opens section ${i}. Nothing here is remarkable except where it sits.\n\nParagraph ${i}c closes the section with a sentence.`).join('\n\n') + '\n'

test('A1: two drafts far apart stay two; rejecting one leaves the other; accepting keeps the final line break', { skip }, async () => {
  await start()
  const { p, page } = await suggestDoc(LONGTEXT)
  // a draft open early in the doc, then Ctrl+End and words at the end (as walked)
  await caretAfter(page, 'Paragraph 1a opens section 1.')
  await page.keyboard.type(' Early.')
  await page.waitForTimeout(PAUSE)
  await page.keyboard.press('Control+End')
  await page.keyboard.press('ArrowLeft')
  await page.keyboard.type(' End words here.')
  await page.waitForTimeout(PAUSE)
  await caretAfter(page, 'Paragraph 3c closes the section with a sentence.')
  await page.keyboard.type(' Mid.')
  await page.waitForTimeout(PAUSE)
  let s1 = await storedWithMine(p)
  assert.equal(s1.mine.length, 3, JSON.stringify(s1.mine.map(n => [n.anchor.quote, n.replacement])))
  const early = s1.mine.find(n => n.replacement.includes('Early.'))
  await closeOne(page, p, early.id, 'reject')
  s1 = await storedWithMine(p)
  assert.ok(s1.mine.every(n => n.anchor.quote.length < 40), 'neither swallows the text between')
  assert.equal(s1.text, await shown(page))
  const mid = s1.mine.find(n => n.replacement.includes('Mid.'))
  await closeOne(page, p, mid.id, 'reject')
  assert.equal(body((await disk(p)).text), body(LONGTEXT))
  assert.ok(!(await shown(page)).includes('Mid.'), 'the rejected words left the editor')
  assert.equal((await storedWithMine(p)).text, await shown(page))
  const end = (await storedWithMine(p)).mine[0]
  await closeOne(page, p, end.id, 'accept')
  const d = (await disk(p)).text
  assert.ok(d.endsWith('End words here.\n'), JSON.stringify(d.slice(-30)))
  assert.equal(d, await shown(page))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('A2: deleting original words beside a draft, then more, then accepting', { skip }, async () => {
  await start()
  const { p, page } = await suggestDoc(LONGTEXT)
  await caretAfter(page, 'Nothing here')
  await page.keyboard.type(' at all')
  await page.waitForTimeout(PAUSE)
  const s = await indexIn(page, ' is remarkable', false)
  await selectText(page, s, s + ' is remarkable'.length)
  await page.keyboard.press('Delete')
  await page.waitForTimeout(PAUSE)
  await caretAfter(page, 'Nothing here at all')
  await page.keyboard.type(' matters')
  await page.waitForTimeout(PAUSE)
  const seen = await shown(page)
  assert.ok(seen.includes('Paragraph 1a opens section 1. Nothing here at all matters except'), seen.slice(0, 200))
  const st1 = await storedWithMine(p)
  assert.equal(st1.text, seen)
  for (const n of st1.mine) await closeOne(page, p, n.id, 'accept')
  assert.equal(body((await disk(p)).text), body(seen))
  assert.equal(await shown(page), (await disk(p)).text)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('A3: typing over a selection, extending it, accepting; a closed note past a shorter text never stops the doc', { skip }, async () => {
  await start()
  const { p, page } = await suggestDoc(LONGTEXT)
  const s = await indexIn(page, 'closes the section', false)
  await selectText(page, s, s + 'closes the section'.length)
  await page.keyboard.type('ends it')
  await page.waitForTimeout(PAUSE)
  await page.keyboard.type(' nicely')
  await page.waitForTimeout(PAUSE)
  const seen = await shown(page)
  const st1 = await storedWithMine(p)
  assert.equal(st1.mine.length, 1)
  await closeOne(page, p, st1.mine[0].id, 'accept')
  assert.equal((await disk(p)).text, seen)
  assert.equal(await shown(page), seen)
  // the accepted note's stored place now lies past a much shorter text
  await outside(p, (await disk(p)).text.slice(0, 120) + '\n')
  await page.waitForTimeout(2500)
  await page.done()
  const again = await open({ doc: p })
  assert.ok(await again.$('.dc .cm-content'), 'the doc opens in the editor')
  await again.waitForTimeout(2000)
  assert.deepEqual(again.errors, [])
  await again.done()
})

test('A4: two insertions, the later one accepted: once on disk, once in the editor, labelled alike', { skip }, async () => {
  await start()
  const { p, page } = await suggestDoc(LONGTEXT)
  await caretAfter(page, 'Paragraph 2c closes the section with a sentence.')
  await page.keyboard.type(' First.')
  await page.waitForTimeout(PAUSE)
  await caretAfter(page, 'Paragraph 4a opens section 4.')
  await page.keyboard.type(' Second.')
  await page.waitForTimeout(PAUSE)
  const labels = await page.$$eval('.dc-card.sug .dc-stl', els => els.map(e => e.textContent))
  assert.deepEqual(labels.sort(), ['Add “First.”', 'Add “Second.”'])
  const second = (await storedWithMine(p)).mine.find(n => n.replacement.includes('Second.'))
  const before = await caretPos(page)
  await closeOne(page, p, second.id, 'accept')
  const t = await shown(page)
  assert.equal(t.split('Second.').length, 2, 'once in the editor')
  assert.equal((await disk(p)).text.split('Second.').length, 2, 'once on disk')
  assert.ok(sameCaret(await caretPos(page), before), 'accepting did not move the caret')
  assert.equal((await storedWithMine(p)).text, t)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('A5: undo after a pause withdraws it; the caret stays through Accept, Reject and a mode change; the highlight is only the suggested words', { skip }, async () => {
  await start()
  const { p, page } = await suggestDoc(LONGTEXT)
  await caretAfter(page, 'Paragraph 1a opens section 1.')
  await page.keyboard.type(' Undone.')
  await page.waitForTimeout(PAUSE)
  await page.keyboard.press('Control+z')
  await page.waitForTimeout(PAUSE)
  assert.deepEqual((await storedWithMine(p)).mine, [], 'nothing left: no "Add “”" card')
  assert.equal(await page.locator('.dc-card.sug').count(), 0)
  // Accept from the card, the caret just after the words typed: it stays.
  await caretAfter(page, 'Paragraph 3a opens section 3.')
  await page.keyboard.type(' Taken.')
  await page.waitForTimeout(PAUSE)
  const taken = (await storedWithMine(p)).mine[0].id
  const atTaken = await caretPos(page)
  await page.click(`.dc-card[data-id="${taken}"]`)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, () => !document.querySelector('.dc-card.sug'))
  assert.ok(sameCaret(await caretPos(page), atTaken), `accepting did not move the caret: ${atTaken} / ${await caretPos(page)}`)
  await caretAfter(page, 'Paragraph 2a opens section 2.')
  await page.keyboard.type(' Kept.')
  await page.waitForTimeout(PAUSE)
  const id = (await storedWithMine(p)).mine[0].id
  // focus its card: only the suggested words are highlighted
  await page.click(`.dc-card[data-id="${id}"]`)
  const lit = await page.$$eval('.cm-content .on', els => els.map(e => e.textContent).join('|'))
  assert.equal(lit.trim(), 'Kept.')
  await caretAfter(page, 'Paragraph 5a opens')
  const c = await caretPos(page)
  await mode(page, 'Editing')
  await mode(page, 'Suggesting')
  assert.ok(sameCaret(await caretPos(page), c), `a mode change did not move the caret: ${c} / ${await caretPos(page)}`)
  await page.evaluate(() => window.__ed.view.focus())
  await closeOne(page, p, id, 'reject')
  assert.ok(sameCaret(await caretPos(page), c), 'reject did not move the caret')
  assert.deepEqual(page.errors, [])
  await page.done()
})

// Seeded runs with several drafts open at once: typing anywhere (first and
// last lines too), deleting original words, typing over a selection, Enter,
// undo and redo, pauses, and accepting or rejecting one draft at a time in a
// random order with others still open. After every step: the stored text
// with my open suggestions applied is the editor's text; an accept or reject
// changes no other suggestion and does not move the caret. At the end every
// draft is rejected in a random order and the original comes back exactly.
// A few runs share a browser context, which is then closed (the box is
// shared: the suite runs under a memory cap and keeps little open).
const FUZZ = Number(process.env.DC_FUZZ || 120)
test(`Suggesting, ${FUZZ} seeded runs with several drafts open at once`, { skip, timeout: 3600000 }, async () => {
  await start()
  const WORDS = [' Aaa.', ' bb', 'x', ',', ' cc dd', '.', 'Zed ']
  const failures = []
  let most = 0
  const only = process.env.DC_SEED ? Number(process.env.DC_SEED) : null
  let ctx = null, page = null, errors = []
  for (let seed = only || 1; seed <= (only || FUZZ); seed++) {
    if (!ctx || (seed - 1) % 10 === 0) {
      await ctx?.close()
      ctx = await browser.newContext({ viewport: { width: 1280, height: 850 } })
      page = await ctx.newPage()
      page.on('pageerror', (e) => errors.push(`seed ${seed}: ${e.message}`))
    }
    let s = (seed * 2654435761) >>> 0
    const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 }
    const pick = (n) => Math.floor(r() * n)
    const p = await newDoc(LONGTEXT)
    await page.goto(`${base}/?path=${encodeURIComponent(p)}`)
    await page.waitForSelector('.cm-content')
    await mode(page, 'Suggesting')
    const log = []
    const bodyStart = () => page.evaluate(() => { const t = window.__ed.text(); const m = /^---\n[\s\S]*?\n---\n/.exec(t); return m ? m[0].length : 0 })
    const fail = (msg) => failures.push(`seed ${seed}: ${msg} after ${log.join(', ')}`)
    try {
      for (let step = 0; step < 16 && failures.length < 4; step++) {
        const b0 = await bodyStart()
        const len = await page.evaluate(() => window.__ed.view.state.doc.length)
        const at = b0 + pick(len - b0 + 1)
        const k = r()
        if (k < 0.28) { const w = WORDS[pick(WORDS.length)]; log.push(`type ${JSON.stringify(w)}@${at}`); await page.evaluate((a) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a } }) }, at); await page.keyboard.type(w) }
        else if (k < 0.4) { const n = 1 + pick(10); log.push(`delete ${n}@${at}`); await selectText(page, at, Math.min(len, at + n)); await page.keyboard.press('Delete') }
        else if (k < 0.5) { const n = 1 + pick(14); log.push(`over ${n}@${at}`); await selectText(page, at, Math.min(len, at + n)); await page.keyboard.type('new words') }
        else if (k < 0.55) { log.push(`enter@${at}`); await page.evaluate((a) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a } }) }, at); await page.keyboard.press('Enter') }
        else if (k < 0.6) { log.push('undo'); await page.keyboard.press('Control+z') }
        else if (k < 0.63) { log.push('redo'); await page.keyboard.press('Control+Shift+z') }
        else if (k < 0.8) {
          log.push('pause'); const c = await caretPos(page); await settle(page)
          if (!sameCaret(await caretPos(page), c)) fail('a pause moved the caret')
        } else {
          await settle(page)
          const { mine } = await storedWithMine(p)
          if (!mine.length) continue
          const n = mine[pick(mine.length)]
          const how = r() < 0.5 ? 'accept' : 'reject'
          log.push(`${how} ${JSON.stringify(n.replacement.slice(0, 20))}`)
          const others = mine.filter(x => x.id !== n.id).map(x => `${x.id}:${x.anchor.quote}→${x.replacement}`).sort()
          const c = await caretPos(page)
          // a caret inside the words being taken back has to move with them
          const inside = await page.evaluate((id) => { const x = window.__ed.drafts().find(d => d.ids[0] === id); const h = window.__ed.view.state.selection.main.head; return Boolean(x && h >= x.from && h <= x.to) }, n.id)
          await closeOne(page, p, n.id, how)
          const after = (await storedWithMine(p)).mine.map(x => `${x.id}:${x.anchor.quote}→${x.replacement}`).sort()
          if (JSON.stringify(after) !== JSON.stringify(others)) fail(`${how} changed another suggestion: ${JSON.stringify(others)} -> ${JSON.stringify(after)}`)
          // the caret stays: the same few letters on either side of it
          // (further off, the reject may have put original words back)
          const c2 = await caretPos(page)
          const stays = c2.split('|')[1].slice(0, 3) === c.split('|')[1].slice(0, 3) && c2.split('|')[0].slice(-3) === c.split('|')[0].slice(-3)
          if (!inside && !stays) fail(`${how} moved the caret ${c} -> ${await caretPos(page)}`)
        }
        await settle(page)
        const now = await storedWithMine(p)
        most = Math.max(most, now.mine.length)
        const t = await shown(page)
        if (now.text !== t) {
          let k0 = 0; while (now.text[k0] === t[k0]) k0++
          fail(`stored+mine ≠ editor at ${k0}: ${JSON.stringify(now.text.slice(k0 - 40, k0 + 40))} vs ${JSON.stringify(t.slice(k0 - 40, k0 + 40))}; mine ${JSON.stringify(now.mine.map(m => [m.anchor.quote, m.replacement]))}`)
          break
        }
      }
      // Reject everything left, in a random order: the original exactly.
      let left = (await storedWithMine(p)).mine
      while (left.length) { await closeOne(page, p, left[pick(left.length)].id, 'reject'); left = (await storedWithMine(p)).mine }
      const stored = await storedWithMine(p)
      const original = LONGTEXT
      if (body(stored.stored) !== body(original) && !log.some(l => l.startsWith('accept'))) fail('rejecting all did not give the original')
      if (stored.text !== await shown(page)) fail('after rejecting all, the editor differs from what is stored')
    } catch (err) { fail(`threw ${err.message.split('\n')[0]}`) }
    if (failures.length >= 4) break
  }
  await ctx?.close()
  console.log(`# most suggestions open at once in a run: ${most}`)
  assert.deepEqual(failures, [])
  assert.deepEqual(errors, [])
  assert.ok(only || most >= 3, 'the runs kept several drafts open')
})

// -- the watch ----------------------------------------------------------------

test('an open doc shows a note an agent adds, a reply and a close, within a few seconds', { skip }, async () => {
  await start()
  const p = await newDoc(TEXT)
  const page = await open({ doc: p })
  const added = await api(p, 'doc/notes', 'POST', { kind: 'comment', quote: 'the grey dog', text: 'From an agent, while the doc is open.' }, 'ada')
  await until(page, () => document.querySelectorAll('.dc-margin .dc-card').length === 1, null, 4000)
  await shot(page, 'agent-note-arrives')
  await api(p, `doc/notes/${added.note}/replies`, 'POST', { text: 'And a reply.' }, 'ada')
  await until(page, () => document.querySelector('.dc-margin .dc-card .dc-more')?.textContent === '1 reply', null, 4000)
  await api(p, `doc/notes/${added.note}/resolve`, 'POST', {}, 'ada')
  await until(page, () => document.querySelectorAll('.dc-margin .dc-card').length === 0, null, 4000)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('an idle open doc changes nothing on the page (the watch draws only what changed)', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/On staying.md' })
  await page.waitForTimeout(800)
  const n = await page.evaluate(() => new Promise((res) => {
    let count = 0
    const mo = new MutationObserver((ms) => { for (const m of ms) if (!m.target.closest?.('.cm-content, .cm-cursorLayer, .cm-selectionLayer') && !(m.target.classList?.contains('cm-cursorLayer'))) count++ })
    mo.observe(document.querySelector('.dc'), { subtree: true, childList: true, attributes: true, characterData: true })
    // the watch asks every 1.5 s; and Loam's page asks for the header often
    const t = setInterval(() => window.__ed.refreshHead(), 300)
    setTimeout(() => { mo.disconnect(); clearInterval(t); res(count) }, 6000)
  }))
  assert.equal(n, 0, 'no change to the page in 6 s of idling')
  await page.done()
})

test('Viewing at the end of a long doc shows the end of it, and scrolling grows the doc little', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/Long.md', zoom: 0.9 })
  const h0 = await page.evaluate(() => document.querySelector('.dc-scroll').scrollHeight)
  for (const y of [1500, 3000, 4500, 99999]) await scrollTo(page, y)
  await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true }) })
  await page.waitForTimeout(400)
  const h1 = await page.evaluate(() => document.querySelector('.dc-scroll').scrollHeight)
  assert.ok(h1 / h0 < 1.15, `the doc grew from ${h0} to ${h1} while scrolled`)
  await mode(page, 'Viewing')
  await page.waitForTimeout(800)
  const r = await page.evaluate(() => {
    const s = document.querySelector('.dc-scroll').getBoundingClientRect()
    const lines = [...document.querySelectorAll('.cm-line')].filter(l => { const b = l.getBoundingClientRect(); return b.bottom > s.top && b.top < s.bottom && l.textContent.trim() })
    const last = [...document.querySelectorAll('.cm-line')].pop().getBoundingClientRect()
    return { lines: lines.length, emptyBelow: s.bottom - last.bottom }
  })
  assert.ok(r.lines >= 4, `text is in view (${r.lines} lines)`)
  assert.ok(r.emptyBelow < 400, `little empty space below the end (${r.emptyBelow})`)
  await shot(page, 'viewing-at-end')
  await page.done()
})

test('phone: one bar, and none when there is nothing to show', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/Long.md', w: 430, h: 900, phone: true })
  const bars = () => page.evaluate(() => [...document.querySelectorAll('.dc-kbar, .dc-vbar')].filter(b => b.offsetHeight > 0).length)
  assert.equal(await bars(), 0, 'nothing at load')
  await page.evaluate(() => window.__ed.view.focus())
  await page.waitForTimeout(200)
  assert.equal(await bars(), 1)
  await page.evaluate(() => window.__ed.view.dispatch({ selection: { anchor: 200, head: 210 } }))
  await page.evaluate(() => document.activeElement.blur())
  await page.waitForTimeout(300)
  assert.equal(await bars(), 1, 'one bar after a blur with a selection')
  await shot(page, 'phone-one-bar')
  await page.done()
})

test('a file without a final line break keeps it that way; a new one gets one', { skip }, async () => {
  await start()
  for (const [text, want] of [['No break at the end', 'No break at the end, more'], ['', 'Words in a new file\n']]) {
    const p = await newDoc(text)
    const page = await open({ doc: p })
    await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length } }) })
    await page.keyboard.type(text ? ', more' : 'Words in a new file')
    await page.evaluate(() => window.__ed.flush())
    assert.equal((await disk(p)).text.replace(/^---\n[\s\S]*?\n---\n\n?/, ''), want)
    await page.done()
  }
})

test('a focused card near the bottom of the pane is brought wholly into view', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/On staying.md' })
  // the last note (Sam's, with a reply) placed near the bottom edge
  const id = await page.evaluate(() => {
    const n = window.__ed.state.notes.filter(n => n.state === 'open').sort((a, b) => b.from - a.from)[0]
    const card = document.querySelector(`.dc-card[data-id="${n.id}"]`)
    const s = document.querySelector('.dc-scroll')
    s.scrollTop += card.getBoundingClientRect().top - s.getBoundingClientRect().bottom + 60
    return n.id
  })
  await page.waitForTimeout(300)
  await page.click(`.dc-card[data-id="${id}"]`, { position: { x: 30, y: 10 } })
  await page.waitForTimeout(900)
  const r = await page.evaluate((id) => {
    const c = document.querySelector(`.dc-card[data-id="${id}"]`).getBoundingClientRect()
    const s = document.querySelector('.dc-scroll').getBoundingClientRect()
    const reply = document.querySelector(`.dc-card[data-id="${id}"] .dc-cmp`).getBoundingClientRect()
    return { bottom: c.bottom, top: c.top, sBottom: s.bottom, sTop: s.top, reply: reply.bottom }
  }, id)
  assert.ok(r.reply <= r.sBottom && r.top >= r.sTop, JSON.stringify(r))
  await page.done()
})

test('Viewing: the gutter marks sit centred on their passage\'s first line (90%)', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/Long.md', zoom: 0.9 })
  await mode(page, 'Viewing')
  let seen = 0
  for (const y of [0, 1400, 3000, 99999]) {
    await scrollTo(page, y)
    const o = await offsets(page, true)
    seen += o.length
    if (o.length) near(o, `Viewing, scrolled to ${y}`)
  }
  assert.ok(seen >= 3)
  await page.done()
})

test('the block menu at the end of a doc opens below the caret, the doc making room', { skip }, async () => {
  await start()
  const p = await newDoc('# End\n\n' + Array.from({ length: 30 }, (_, i) => `Paragraph ${i} of a doc long enough to scroll.`).join('\n\n') + '\n\n')
  const page = await open({ doc: p, zoom: 0.9 })
  await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true }) })
  await page.waitForTimeout(300)
  await page.keyboard.type('/')
  await page.waitForSelector('.dc-bmenu.on')
  await page.waitForTimeout(300)
  const m = await page.evaluate(() => {
    const menu = document.querySelector('.dc-bmenu.on').getBoundingClientRect()
    const c = window.__ed.view.coordsAtPos(window.__ed.view.state.selection.main.head)
    const el = document.querySelector('.dc-bmenu.on')
    return { below: menu.top >= c.bottom - 1, scrolls: el.scrollHeight > el.clientHeight + 1, bottom: menu.bottom, h: innerHeight }
  })
  assert.deepEqual({ below: m.below, scrolls: m.scrolls }, { below: true, scrolls: false })
  assert.ok(m.bottom <= m.h)
  await page.keyboard.press('Escape')
  await page.done()
})

test('Escape closes History and Resolved; a change of width keeps the line in view', { skip }, async () => {
  await start()
  const page = await open({ doc: 'Essays/drafts/Long.md' })
  await page.click('.dc-gbtn[aria-label^="More"]')
  await page.click('.dc-mi >> text=History')
  await page.waitForSelector('.dc-drawer.on')
  await page.keyboard.press('Escape')
  await page.waitForTimeout(200)
  assert.equal(await page.locator('.dc-drawer.on').count(), 0, 'Escape closed History')
  await scrollTo(page, 2400)
  const before = await page.evaluate(() => { const s = document.querySelector('.dc-scroll').getBoundingClientRect(); return document.elementFromPoint(s.left + 260, s.top + 120)?.closest('.cm-line')?.textContent.slice(0, 40) })
  await page.evaluate(() => window.__ed.setPhone(true))
  await page.waitForTimeout(500)
  const after = await page.evaluate(() => { const s = document.querySelector('.dc-scroll'); return s.scrollTop })
  assert.ok(after > 1000, `the scroll was kept (${after}, was showing ${before})`)
  await page.done()
})

// -- the visitor's page, through the door -------------------------------------

for (const [w, h, label] of [[1280, 850, 'desktop'], [430, 900, 'phone']]) {
  test(`a visitor's page loads through the door with no owner cookie, every file found (${label})`, { skip }, async () => {
    await start()
    const link = shareLinks.comment
    assert.ok(link, 'the dev server made a share link')
    const ctx = await browser.newContext({ viewport: { width: w, height: h } })
    const page = await ctx.newPage()
    const bad = []
    page.on('response', (r) => { if (r.status() >= 400) bad.push(`${r.status()} ${new URL(r.url()).pathname}`) })
    page.on('console', (m) => { if (m.type() === 'error') bad.push(`console: ${m.text()}`) })
    page.on('pageerror', (e) => bad.push(`error: ${e.message}`))
    await page.goto(`${base}${link.path}`)
    await page.waitForSelector('input[type=password]', { timeout: 8000 })
    await shot(page, `visitor-gate-${label}`)
    await page.fill('input[type=password]', link.password)
    await page.keyboard.press('Enter')
    await page.waitForSelector('.dc .cm-content', { timeout: 8000 })
    await page.waitForTimeout(800)
    await shot(page, `visitor-doc-${label}`)
    assert.deepEqual(bad, [])
    await ctx.close()
  })
}

test('a passage deleted on disk: its note is detached, ⋯ carries the mark and says so', { skip }, async () => {
  await start()
  const p = await newDoc(LONGTEXT)
  await api(p, 'doc/notes', 'POST', { kind: 'comment', quote: 'Paragraph 6c closes the section with a sentence.', text: 'On the last paragraph' }, 'ada')
  const page = await open({ doc: p })
  const d = (await disk(p)).text
  await outside(p, d.replace('\n\nParagraph 6c closes the section with a sentence.', ''))
  await until(page, () => document.querySelector('.dc-gbtn.dc-lost'), null, 5000)
  await page.click('.dc-gbtn.dc-lost')
  const first = await page.textContent('.dc-menu .dc-mi')
  assert.match(first, /^1 note lost its passage/)
  const notes = (await api(p, 'doc', 'GET')).notes
  assert.equal(notes[0].where.state, 'detached', 'not moved onto "Paragraph 5c …"')
  await page.click('.dc-mi-lost')
  await page.waitForSelector('.dc-drawer.on .dc-dsec')
  await shot(page, 'detached')
  assert.deepEqual(page.errors, [])
  await page.done()
})
