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

async function open({ doc = 'Essays/drafts/Long.md', w = 1280, h = 850, zoom = 1, phone = false, extra = '' } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(`${base}/?dark&zoom=${zoom}${phone ? '&phone' : ''}${extra}&path=${encodeURIComponent(doc)}`)
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
  // It comes once the selection has been still a moment (the selecting tests).
  await page.waitForSelector('.dc-selbar.on')
  await page.waitForTimeout(250)
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
  // The slim bar comes once the selection has been still (350 ms).
  await page.waitForTimeout(500)
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

// -- where a note sits after the text changes (round 4) -------------------------

const REPEATS = '# Walk\n\n' + [1, 2, 3, 4, 5, 6].map(i => `## Section ${i}\n\nParagraph ${i}a. The river kept its own time, and the town learned to keep it too.\n\n> [!note] A callout in section ${i}\n> The note in the callout runs to two lines.\n> A second line of the callout.\n\n| Year | Where | Why |\n| --- | --- | --- |\n| 201${i} | Lisbon | the light |\n| 202${i} | Porto | the river |\n\nParagraph ${i}b, after the blocks. Nothing here is remarkable except where it sits.\n\nParagraph ${i}c closes the section with a sentence.\n`).join('\n')
const nthIn = (t, w, n) => { let at = -1; for (let i = 0; i < n; i++) at = t.indexOf(w, at + 1); return at }
async function noteOn(p, kind, words, n, extra = {}) {
  const d = await api(p, 'doc', 'GET')
  const s = nthIn(d.text, words, n)
  const got = await api(p, 'doc/notes', 'POST', { kind, start: s, end: s + words.length, quote: words, revision: d.revision, text: 'x', ...extra })
  return got.note
}
const whereOf = async (p, id) => (await api(p, 'doc', 'GET')).notes.find(n => n.id === id)
async function typeAt(page, pos, text) {
  await page.evaluate((a) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a } }) }, pos)
  await page.keyboard.type(text)
  await page.evaluate(() => window.__ed.flush())
}

test('Places 1: a suggestion stays on its paragraph when words are typed before it; Accept writes there', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'suggestion', 'remarkable', 6, { replacement: 'notable' })
  const page = await open({ doc: p })
  await typeAt(page, await page.evaluate(() => { const t = window.__ed.text(); let at = -1; for (let i = 0; i < 6; i++) at = t.indexOf('Nothing here', at + 1); return at + 'Nothing here'.length }), ' truly')
  const n = await whereOf(p, id)
  const t = (await disk(p)).text
  assert.equal(n.where.start, nthIn(t, 'remarkable', 6))
  await page.evaluate(() => window.__ed.reload())
  await page.click(`.dc-card[data-id="${id}"]`)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, (id) => window.__ed.state.notes.find(x => x.id === id)?.state === 'accepted', id)
  const after = (await disk(p)).text
  assert.ok(after.includes('Paragraph 6b, after the blocks. Nothing here truly is notable'), after.slice(-400))
  assert.equal(after.split('notable').length, 2)
  await page.done()
})

test('Places 2–4: a comment stays on a word typed before it, on an overlapping accept, and a suggestion on an edit inside it', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const c5 = await noteOn(p, 'comment', 'line of the callout', 5)
  const c4 = await noteOn(p, 'comment', 'line of the callout', 4)
  const s4 = await noteOn(p, 'suggestion', 'callout.', 4, { replacement: 'callout and three.' })
  const s3 = await noteOn(p, 'suggestion', 'light', 3, { replacement: 'sun' })
  const page = await open({ doc: p })
  await typeAt(page, await page.evaluate(() => { const t = window.__ed.text(); let at = -1; for (let i = 0; i < 5; i++) at = t.indexOf('line of the callout', at + 1); return at }), 'fine ')
  let t = (await disk(p)).text
  assert.equal((await whereOf(p, c5)).where.start, nthIn(t, 'line of the callout', 5), 'case 2')
  await api(p, `doc/notes/${s4}/accept`)
  t = (await disk(p)).text
  const w4 = (await whereOf(p, c4)).where
  assert.ok(w4.start > t.indexOf('## Section 4') && w4.start < t.indexOf('## Section 5'), 'case 3: still in section 4')
  await page.evaluate(() => window.__ed.reload())
  await page.waitForTimeout(300)
  const l = await page.evaluate(() => { const t = window.__ed.text(); let at = -1; for (let i = 0; i < 3; i++) at = t.indexOf('light', at + 1); return at })
  await typeAt(page, l + 2, 'x')
  t = (await disk(p)).text
  const w3 = (await whereOf(p, s3)).where
  assert.equal(w3.start, nthIn(t, 'lixght', 1), 'case 4: on its own cell, changed')
  assert.equal(w3.state, 'changed')
  const acc = await api(p, `doc/notes/${s3}/accept`)
  assert.ok(acc.error, 'Accept refuses rather than write elsewhere')
  assert.ok(!(await disk(p)).text.includes('sun'))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Places: seeded runs of typing, deleting and disk edits near, inside and far from notes on repeated words', { skip, timeout: 1800000 }, async () => {
  await start()
  const PH = ['remarkable', 'line of the callout', 'the light', 'the river', 'Nothing here is', 'callout']
  const failures = []
  let ctx = null, page = null, checked = 0, waited = 0
  const RUNS = Number(process.env.DC_PLACES || 30)
  for (let seed = 1; seed <= RUNS && failures.length < 4; seed++) {
    if (!ctx || (seed - 1) % 10 === 0) { await ctx?.close(); ctx = await browser.newContext({ viewport: { width: 1280, height: 850 } }); page = await ctx.newPage() }
    let s = (seed * 2654435761) >>> 0
    const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 }
    const pick = (n) => Math.floor(r() * n)
    const p = await newDoc(REPEATS)
    const truth = {}
    for (let i = 0; i < 4; i++) {
      const ph = PH[pick(PH.length)], n = 1 + pick(6)
      const t = (await disk(p)).text
      const a = nthIn(t, ph, n)
      if (Object.values(truth).some(([x, y]) => !(a + ph.length <= x || y <= a))) continue
      const id = await noteOn(p, r() < 0.5 ? 'comment' : 'suggestion', ph, n, { replacement: ph.toUpperCase() })
      const t2 = (await disk(p)).text
      truth[id] = [nthIn(t2, ph, n), nthIn(t2, ph, n) + ph.length]
    }
    await page.goto(`${base}/?path=${encodeURIComponent(p)}`)
    await page.waitForSelector('.cm-content')
    for (let step = 0; step < 8; step++) {
      const t = (await disk(p)).text
      const ids = Object.keys(truth).filter(k => truth[k])
      const [a, b] = ids.length ? truth[ids[pick(ids.length)]] : [0, 0]
      let at = [a - 1 - pick(8), b + pick(8), a + pick(Math.max(1, b - a))][pick(3)]
      const fm = /^---\n[\s\S]*?\n---\n/.exec(t)?.[0].length || 0
      at = Math.max(fm + 1, Math.min(t.length - 1, at))
      const del = [0, 0, 1, 4][pick(4)]
      const ins = ['', ' truly', 'x', 'the river '][pick(4)] || (del ? '' : 'y')
      const onDisk = r() >= 0.7
      if (!onDisk) {
        await page.evaluate(([a2, d]) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a2, head: a2 + d } }) }, [at, del])
        if (del) await page.keyboard.press('Delete')
        if (ins) await page.keyboard.type(ins)
        await page.evaluate(() => window.__ed.flush())
      } else {
        await outside(p, t.slice(0, at) + ins + t.slice(at + del))
        await page.evaluate(() => window.__ed.reload())
      }
      for (const [id, v] of Object.entries(truth)) {
        if (!v) continue
        const [x, y] = v
        if (at + del <= x) truth[id] = [x + ins.length - del, y + ins.length - del]
        else if (at < y) truth[id] = null          // inside: changed or gone there, not tracked further
      }
      const notes = (await api(p, 'doc', 'GET')).notes
      for (const [id, v] of Object.entries(truth)) {
        if (!v) continue
        const n = notes.find(m => m.id === id)
        // (round 6) an edit on disk within a note's surroundings, on words
        // that repeat: it may wait detached there, never sit elsewhere
        if (onDisk && n.where.state === 'detached' && at <= v[1] + 200 && at + Math.max(del, ins.length) >= v[0] - 200
            && Math.max(t.split(n.anchor.quote).length, (await disk(p)).text.split(n.anchor.quote).length) > 2) { truth[id] = null; waited++; continue }
        checked++
        if (n.where.state === 'detached' || n.where.start !== v[0]) failures.push(`seed ${seed} step ${step}: ${JSON.stringify(n.anchor.quote)} at ${n.where.start} (${n.where.state}), put on ${v[0]}; change ${at} -${del} +${JSON.stringify(ins)}`)
      }
    }
  }
  await ctx?.close()
  console.log(`# ${checked} note places checked, ${waited} waited after an edit on disk within their surroundings`)
  assert.deepEqual(failures, [])
})

// -- round 4: the visitor's side ------------------------------------------------

const devLink = async (mode, name = null, path = null) => (await fetch(`${base}/dev/link`, { method: 'POST', body: JSON.stringify({ mode, name, path }) })).json()
async function visitor(link, { w = 1280, h = 850, name = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } })
  const page = await ctx.newPage()
  page.errors = []
  page.on('pageerror', (e) => page.errors.push(e.message))
  await page.goto(`${base}${link.path}`)
  await page.fill('input[type=password]', link.password)
  await page.keyboard.press('Enter')
  if (name) {
    await page.waitForSelector('input[autocomplete=name]')
    await page.fill('input[autocomplete=name]', name)
    await page.keyboard.press('Enter')
  }
  await page.waitForSelector('.dc .cm-content', { timeout: 8000 })
  page.done = () => ctx.close()
  return page
}

test('a visitor with the doc open when its link is turned off is told so, and the page stops asking', { skip }, async () => {
  await start()
  const link = await devLink('comment', 'Robin')
  const page = await visitor(link)
  let asks = 0
  page.on('request', (r) => { if (r.url().includes('/api/')) asks++ })
  await fetch(`${base}/dev/link-off`, { method: 'POST', body: JSON.stringify({ token: link.token }) })
  await page.waitForSelector('.dc-gone-card', { timeout: 6000 })
  assert.match(await page.textContent('.dc-gone-card'), /turned off/)
  const n = asks
  await page.waitForTimeout(4000)
  assert.ok(asks - n <= 1, `the page stopped asking (${asks - n} more)`)
  await shot(page, 'visitor-turned-off')
  await page.done()
  // and a fresh visit to it: the gate's look, one calm sentence
  const ctx = await browser.newContext()
  const p2 = await ctx.newPage()
  const res = await p2.goto(`${base}${link.path}`)
  assert.equal(res.status(), 404)
  assert.ok(await p2.$('.dc-gone-card'), 'styled')
  assert.equal(await p2.evaluate(() => getComputedStyle(document.querySelector('.dc-gate-card')).borderRadius !== '0px'), true)
  await ctx.close()
})

test('a visitor resolves a thread their own agent opened, in the page', { skip }, async () => {
  await start()
  const link = await devLink('comment', 'Robin')
  const page = await visitor(link)
  const key = (await page.evaluate(async (P) => (await (await fetch(`${P}/api/agent-key`, { method: 'POST', credentials: 'same-origin', headers: { 'X-Loam-Share': '1', 'Content-Type': 'application/json' }, body: '{}' })).json()).key, link.path))
  const made = await (await fetch(`${base}${link.path}/api/doc/notes`, { method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'comment', quote: 'the point', text: 'From my agent' }) })).json()
  await page.evaluate(() => window.__ed?.reload?.())
  await page.waitForTimeout(2500)
  await page.click(`.dc-card[data-id="${made.note}"]`)
  await page.waitForSelector('.dc-card.on .dc-resolve', { timeout: 4000 })
  await page.click('.dc-card.on .dc-resolve')
  await page.waitForTimeout(800)
  assert.equal(await page.locator(`.dc-card[data-id="${made.note}"]`).count(), 0)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('a group visitor named "<b>x</b>" is shown as those letters', { skip }, async () => {
  await start()
  const link = await devLink('comment', null)
  const page = await visitor(link, { name: '<b>x</b>' })
  await page.click('.dc-mebtn')
  assert.equal(await page.textContent('.dc-mehead b'), '<b>x</b>')
  assert.equal(await page.locator('.dc-mehead b b').count(), 0)
  await page.done()
})

test('a suggestion typed on the empty last line, accepted, keeps the file\'s final line break', { skip }, async () => {
  await start()
  const p = await newDoc('# End\n\nThe last paragraph ends at the column.\n')
  const page = await open({ doc: p })
  await mode(page, 'Suggesting')
  await page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length } }) })
  await page.keyboard.type('End words here.')
  await page.evaluate(() => window.__ed.flush())
  const d = await api(p, 'doc', 'GET')
  const n = d.notes.find(x => x.state === 'open')
  await api(p, `doc/notes/${n.id}/accept`)
  const t = (await disk(p)).text
  assert.ok(t.endsWith('End words here.\n'), JSON.stringify(t.slice(-30)))
  await page.done()
})

// -- a note whose passage is removed whole (round 5) ----------------------------
//
// A note is carried through the change and nowhere else: if its words go, it
// waits detached, drawn nowhere; if they come back at its place, it is home.

const drawnFor = (page, id) => page.evaluate((id) => document.querySelectorAll(`.cm-content [data-note="${CSS.escape(id)}"]`).length, id)
const cutSections = (t, from, to) => t.slice(0, t.indexOf(`## Section ${from}`)) + t.slice(t.indexOf(`## Section ${to}`))

test('Whole 1: a suggestion in sections deleted on disk is drawn nowhere, Accept writes nothing; put back, it is home', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'suggestion', 'remarkable', 3, { replacement: 'dazzling' })
  const page = await open({ doc: p, h: 3200 })
  const t = (await disk(p)).text
  await outside(p, cutSections(t, 3, 5))
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', id)
  assert.equal(await drawnFor(page, id), 0, 'not drawn in section 1 or anywhere')
  const acc = await api(p, `doc/notes/${id}/accept`)
  assert.ok(acc.error, 'Accept refuses')
  assert.ok(!(await disk(p)).text.includes('dazzling'))
  await outside(p, t)
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', id)
  const w = (await whereOf(p, id)).where
  assert.equal(w.start, nthIn(t, 'remarkable', 3), 'home, in section 3')
  await page.waitForTimeout(300)
  assert.ok(await drawnFor(page, id) > 0)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Whole 2: cut section 3, paste, suggest on 3b, restore the cut version: the suggestion waits, not in section 1', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  await noteOn(p, 'comment', 'Walk', 1)
  const page = await open({ doc: p, h: 3200 })
  const t = await shown(page)
  const a = t.indexOf('## Section 3'), b = t.indexOf('## Section 4')
  const block = t.slice(a, b)
  await page.evaluate(([a, b]) => { const v = window.__ed.view; v.focus(); v.dispatch({ changes: { from: a, to: b }, userEvent: 'delete.cut' }) }, [a, b])
  await page.evaluate(() => window.__ed.flush())
  await page.waitForTimeout(200)
  const cut = (await (await fetch(`${base}/x/doc-comments/doc/history?${new URLSearchParams({ root: 'vault', path: p })}`)).json()).saves[0].id
  await page.evaluate(([a, s]) => { const v = window.__ed.view; v.dispatch({ changes: { from: a, insert: s }, userEvent: 'input.paste' }) }, [a, block])
  await page.evaluate(() => window.__ed.flush())
  await page.waitForTimeout(200)
  const id = await noteOn(p, 'suggestion', 'remarkable', 3, { replacement: 'bright' })
  await api(p, 'doc/restore', 'POST', { save: cut })
  await page.evaluate(() => window.__ed.reload())
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', id)
  assert.equal(await drawnFor(page, id), 0)
  const acc = await api(p, `doc/notes/${id}/accept`)
  assert.ok(acc.error, 'Accept refuses')
  assert.ok(!(await disk(p)).text.includes('bright'))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Whole 3: comments in a section deleted on disk wait; their neighbours stay; put back, they are home', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const c4 = await noteOn(p, 'comment', 'line of the callout', 4)
  const c2 = await noteOn(p, 'comment', 'The river kept its own time', 2)
  const page = await open({ doc: p, h: 3200 })
  const t = (await disk(p)).text
  await outside(p, cutSections(t, 4, 5))
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', c4)
  assert.equal(await drawnFor(page, c4), 0)
  assert.equal((await whereOf(p, c2)).where.start, nthIn((await disk(p)).text, 'The river kept its own time', 2))
  await outside(p, t)
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', c4)
  assert.equal((await whereOf(p, c4)).where.start, nthIn(t, 'line of the callout', 4))
  await page.waitForTimeout(300)
  assert.ok(await drawnFor(page, c4) > 0)
  assert.deepEqual(page.errors, [])
  await page.done()
})

for (const sent of [true, false]) {
  test(`Whole 4: a draft${sent ? '' : ' not yet sent'} in Suggesting whose section is deleted on disk is never sent again on other words`, { skip }, async () => {
    await start()
    const p = await newDoc(REPEATS)
    const page = await open({ doc: p, h: 3200 })
    await mode(page, 'Suggesting')
    const at = await page.evaluate(() => { const t = window.__ed.text(); return t.indexOf('Paragraph 2a.') + 'Paragraph 2a. '.length })
    await page.evaluate((a) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: a } }) }, at)
    await page.keyboard.type('splendid')
    if (sent) await settle(page)
    const t = body((await disk(p)).text)
    const full = (await disk(p)).text
    await outside(p, full.slice(0, full.indexOf('## Section 2')) + full.slice(full.indexOf('## Section 3')))
    await until(page, (t) => !window.__ed.text().includes('Paragraph 2a.'), t)
    await page.waitForTimeout(PAUSE)
    await settle(page)
    const notes = (await api(p, 'doc', 'GET')).notes.filter(n => n.kind === 'suggestion' && n.state === 'open')
    for (const n of notes) assert.equal(n.where.state, 'detached', `no suggestion on other words: ${JSON.stringify(n.anchor.quote)} → ${JSON.stringify(n.replacement)}`)
    assert.ok(!(await shown(page)).includes('splendid'), 'its words left with the passage')
    assert.equal(body(await shown(page)), body((await disk(p)).text))
    const toast = await page.textContent('.dc-toast').catch(() => '')
    assert.match(toast || '', /removed from the file/)
    assert.deepEqual(page.errors, [])
    await page.done()
  })
}

test('Whole 6: a paragraph deleted on disk and put back brings its comment home', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'comment', 'Paragraph 4c closes the section', 1)
  const page = await open({ doc: p, h: 3200 })
  const t = (await disk(p)).text
  const a = t.indexOf('Paragraph 4c'), b = t.indexOf('\n', a) + 1
  await outside(p, t.slice(0, a) + t.slice(b))
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', id)
  await outside(p, t)
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', id)
  assert.equal((await whereOf(p, id)).where.start, a)
  assert.deepEqual(page.errors, [])
  await page.done()
})

// -- round 5, B ----------------------------------------------------------------

const countIn = (s, w) => s.split(w).length - 1
const drawnText = (page) => page.evaluate(() => document.querySelector('.cm-content').innerText)

test('B1: a visitor\'s own suggestion is drawn once after a reload, in Suggesting', { skip }, async () => {
  await start()
  const link = await devLink('comment', 'Robin')
  const page = await visitor(link)
  await mode(page, 'Suggesting')
  await page.evaluate(() => {
    const v = document.querySelector('.cm-content').cmTile.view, at = v.state.doc.toString().indexOf('the clean slate') + 15
    v.focus(); v.dispatch({ selection: { anchor: at } })
  })
  await page.keyboard.type(' entirely')
  await page.waitForTimeout(PAUSE + 500)
  assert.equal(countIn(await drawnText(page), 'entirely'), 1)
  await page.reload()
  await page.waitForSelector('.dc .cm-content', { timeout: 8000 })
  await page.waitForTimeout(1000)
  if (!/Suggesting/.test(await page.textContent('.dc-mpill'))) await mode(page, 'Suggesting')
  await page.waitForTimeout(300)
  assert.equal(countIn(await drawnText(page), 'entirely'), 1, 'drawn once, not doubled')
  assert.equal(countIn(await page.evaluate(() => document.querySelector('.cm-content').cmTile.view.state.doc.toString()), 'entirely'), 1)
  assert.deepEqual(page.errors, [])
  await page.done()
  const P = 'Essays/drafts/On staying.md'
  for (const n of (await api(P, 'doc', 'GET')).notes) if (n.kind === 'suggestion' && n.state === 'open' && /entirely/.test(n.replacement)) await api(P, `doc/notes/${n.id}/reject`)
})

test('B2: after Suggesting with two suggestions in one paragraph, the marks sit on their own words', { skip }, async () => {
  await start()
  const p = await newDoc(TEXT)
  const page = await open({ doc: p })
  for (const m of ['Viewing', 'Editing']) {
    await mode(page, 'Suggesting')
    let t = await shown(page)
    await selectText(page, t.indexOf('baker'), t.indexOf('baker') + 5)
    await page.keyboard.type('grocer')
    await settle(page)
    t = await shown(page)
    await selectText(page, t.indexOf('grey'), t.indexOf('grey') + 4)
    await page.keyboard.type('brown')
    await settle(page)
    await page.waitForTimeout(300)
    await mode(page, m)
    await page.waitForTimeout(300)
    const marks = await page.evaluate(() => [...document.querySelectorAll('.cm-content .dc-sg-del')].map(e => e.textContent))
    assert.deepEqual(marks.sort(), ['baker', 'grey'], `${m}: the struck words`)
    const ins = await page.evaluate(() => [...document.querySelectorAll('.cm-content ins.dc-sg-ins')].map(e => e.textContent))
    assert.deepEqual(ins.sort(), ['brown', 'grocer'])
    await closeMine(p, 'reject')
    await page.evaluate(() => window.__ed.reload())
    await page.waitForTimeout(300)
  }
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B3: Enter at a space reads "Split the paragraph", with no struck space', { skip }, async () => {
  await start()
  const p = await newDoc(TEXT)
  const { page } = { page: await open({ doc: p }) }
  await mode(page, 'Suggesting')
  await caretAfter(page, 'and that was the point.')
  await page.keyboard.press('Enter')
  await settle(page)
  await page.waitForTimeout(400)
  const mine = await mineOpen(p)
  assert.equal(mine.length, 1)
  assert.equal(await page.locator('.cm-content .dc-sg-live-del').count(), 0, 'no struck space')
  const line = await page.textContent(`.dc-card[data-id="${mine[0].id}"] .dc-stl`)
  assert.equal(line.trim(), 'Split the paragraph')
  await mode(page, 'Viewing')
  await page.waitForTimeout(300)
  assert.equal(await page.locator('.cm-content .dc-sg-del').count(), 0, 'no struck space in Viewing')
  assert.deepEqual(page.errors, [])
  await page.done()
})

// The phone bar, measured at 430: a comfortable edge (16 px at the start),
// and the title taking the room the bar has, not a box with a gap after it.
async function headAt430(page) {
  return page.evaluate(() => {
    const head = document.querySelector('.dc-head')
    const cs = getComputedStyle(head)
    const crumb = head.querySelector('.dc-crumb')
    const kids = [...head.children].filter(e => e.offsetWidth > 0)
    const i = kids.indexOf(crumb)
    const next = kids[i + 1]
    const cr = crumb.getBoundingClientRect()
    const b = crumb.querySelector('b') || crumb
    const title = b.getBoundingClientRect()
    return {
      padding: cs.padding, left: cr.left - head.getBoundingClientRect().left, crumb: Math.round(cr.width),
      title: Math.round(title.width), clipped: b.scrollWidth > b.clientWidth + 1,
      empty: next ? Math.round(next.getBoundingClientRect().left - cr.right) : null,
      last: Math.round(head.getBoundingClientRect().right - kids[kids.length - 1].getBoundingClientRect().right),
    }
  })
}
test('B5: the phone bar at 430, on the visitor\'s page and the owner\'s, measured', { skip }, async () => {
  await start()
  const link = await devLink('comment', 'Robin')
  const v = await visitor(link, { w: 430, h: 900 })
  await v.waitForTimeout(500)
  const mv = await headAt430(v)
  await shot(v, 'phone-bar-visitor-430')
  await v.done()
  const o = await open({ doc: 'Essays/drafts/On staying.md', w: 430, h: 900, phone: true })
  const mo = await headAt430(o)
  await shot(o, 'phone-bar-owner-430')
  await o.done()
  console.log('# visitor', JSON.stringify(mv), '\n# owner', JSON.stringify(mo))
  for (const [who, m] of [['visitor', mv], ['owner', mo]]) {
    const pad = m.padding.split(' ').map(parseFloat), pl = pad[3] ?? pad[1] ?? pad[0]
    // (the owner's bar starts with its back button, a 44 px target that is its own edge)
    assert.ok(pl >= 12 || m.left >= 40, `${who}: start edge ${m.padding}`)
    assert.ok(m.empty === null || m.empty <= 12, `${who}: ${m.empty}px empty after the title`)
    assert.ok(!m.clipped || m.empty <= 12, `${who}: a clipped title has the room`)
  }
})

test('B7: a note on a table cell is marked in the drawn table, and opens from there', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'comment', 'Porto', 2)
  const s = await noteOn(p, 'suggestion', 'the light', 1, { replacement: 'the sun' })
  const page = await open({ doc: p, h: 3200 })
  await page.waitForTimeout(300)
  const cells = await page.evaluate(() => [...document.querySelectorAll('.dc-table [data-note]')].map(e => ({ id: e.getAttribute('data-note'), text: e.textContent, cls: e.className })))
  assert.deepEqual(cells.map(c => [c.id, c.text]).sort(), [[id, 'Porto'], [s, 'the light']].sort())
  await page.click(`.dc-table [data-note="${id}"]`)
  await page.waitForTimeout(300)
  assert.equal(await page.evaluate(() => window.__ed.state.focus), id)
  await shot(page, 'table-cell-note')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B9: "Make a new key" asks once before it stops the old one', { skip }, async () => {
  await start()
  const link = await devLink('comment', 'Robin')
  const page = await visitor(link)
  await page.evaluate(async (P) => fetch(`${P}/api/agent-key`, { method: 'POST', credentials: 'same-origin', headers: { 'X-Loam-Share': '1', 'Content-Type': 'application/json' }, body: '{}' }), link.path)
  let made = 0
  page.on('request', (r) => { if (r.url().endsWith('/api/agent-key') && r.method() === 'POST') made++ })
  await page.click('.dc-agentbtn')
  await page.click('.dc-dlg button:has-text("Make a new key")')
  await page.waitForSelector('.dc-agentask')
  assert.equal(made, 0, 'one click asks, it does not act')
  await shot(page, 'new-key-ask')
  await page.click('.dc-dlg .dc-agentno')
  await page.waitForTimeout(200)
  assert.equal(made, 0)
  await page.click('.dc-dlg button:has-text("Make a new key")')
  await page.click('.dc-dlg .dc-agentrow2 button:has-text("Make a new key")')
  await page.waitForTimeout(800)
  assert.equal(made, 1)
  assert.deepEqual(page.errors, [])
  await page.done()
})

// Seeded: whole sections removed and put back, on disk or by a History
// restore, with notes on words of their own in every section and, in some
// runs, a draft of mine open in Suggesting. A note in a removed section waits
// detached and is drawn nowhere; one outside keeps its own words; my draft is
// never sent on other words; put back, every note is home.
const WHOLE = Number(process.env.DC_WHOLE || 16)
test(`Whole: ${WHOLE} seeded runs of sections removed and put back, on disk and by restore, with drafts open`, { skip, timeout: 1800000 }, async () => {
  await start()
  const failures = [], errors = []
  let ctx = null, page = null, checked = 0, drafts = 0, restores = 0, waitedNear = 0
  const SPOTS = (i) => [`Paragraph ${i}b, after the blocks`, `A callout in section ${i}`, `| 202${i} | Porto |`, `Paragraph ${i}c closes`, `keep it too`]
  const history = async (p) => (await (await fetch(`${base}/x/doc-comments/doc/history?${new URLSearchParams({ root: 'vault', path: p })}`)).json()).saves
  for (let seed = 1; seed <= WHOLE && failures.length < 4; seed++) {
    if (!ctx || (seed - 1) % 8 === 0) {
      await ctx?.close()
      ctx = await browser.newContext({ viewport: { width: 1280, height: 3200 } })
      page = await ctx.newPage()
      page.on('pageerror', (e) => errors.push(`seed ${seed}: ${e.message}`))
    }
    let s = (seed * 2246822519) >>> 0
    const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 }
    const pick = (n) => Math.floor(r() * n)
    const fail = (m) => failures.push(`seed ${seed}: ${m}`)
    try {
      const a = 2 + pick(4), b = Math.min(7, a + 1 + pick(2))       // sections a..b-1 go
      const byRestore = r() < 0.5
      const p = await newDoc(REPEATS)
      const full0 = (await disk(p)).text
      const notes = []
      for (let i = 1; i <= 6; i++) {
        const spots = SPOTS(i)
        for (let k = 0; k < 2; k++) {
          const words = spots[pick(spots.length)]
          if (notes.some(n => n.words === words && n.sec === i)) continue
          const t = (await disk(p)).text
          const at = words === 'keep it too' ? nthIn(t, words, i) : t.indexOf(words)
          const kind = r() < 0.5 ? 'comment' : 'suggestion'
          const d = await api(p, 'doc', 'GET')
          const got = await api(p, 'doc/notes', 'POST', { kind, start: at, end: at + words.length, quote: words, revision: d.revision, text: 'x', ...(kind === 'suggestion' ? { replacement: words + ' and more' } : {}) }, 'ada')
          notes.push({ id: got.note, words, sec: i, kind })
        }
      }
      const full = (await disk(p)).text
      const fullId = (await history(p))[0]?.id
      await page.goto(`${base}/?path=${encodeURIComponent(p)}`)
      await page.waitForSelector('.cm-content')
      await page.waitForTimeout(300)
      let draft = null
      if (r() < 0.6) {
        draft = { sec: 1 + pick(6), word: `zq${seed}x` }
        await mode(page, 'Suggesting')
        const at = await page.evaluate((i) => { const t = window.__ed.text(); return t.indexOf(`Paragraph ${i}a.`) + `Paragraph ${i}a. `.length }, draft.sec)
        await page.evaluate((x) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: x } }) }, at)
        await page.keyboard.type(draft.word)
        if (r() < 0.6) await settle(page)
        drafts++
      }
      // the removal
      const cut = full.slice(0, full.indexOf(`## Section ${a}`)) + (b <= 6 ? full.slice(full.indexOf(`## Section ${b}`)) : '')
      const removed = (i) => i >= a && i < b
      if (byRestore) {
        // a version without them, then the full text back, then restore the version
        await outside(p, cut)
        await page.waitForTimeout(1500)
        const cutId = (await history(p))[0].id
        await outside(p, full)
        await page.waitForTimeout(1500)
        await api(p, 'doc/restore', 'POST', { save: cutId })
        restores++
      } else {
        await outside(p, cut)
      }
      await page.waitForTimeout(2200)
      await settle(page)
      await page.waitForTimeout(400)
      const d = await api(p, 'doc', 'GET')
      const T = d.text
      for (const n of notes) {
        const w = d.notes.find(x => x.id === n.id)?.where
        checked++
        if (removed(n.sec)) {
          if (w?.state !== 'detached') fail(`a note in removed section ${n.sec} (${n.words}) is ${w?.state} at ${w?.start}: ${JSON.stringify(T.slice(w?.start - 20, w?.end + 20))}`)
          else if (await drawnFor(page, n.id)) fail(`a detached note is drawn (${n.words})`)
        } else {
          const want = n.words === 'keep it too' ? nthIn(T, n.words, [1, 2, 3, 4, 5, 6].filter(i => !removed(i)).indexOf(n.sec) + 1) : T.indexOf(n.words)
          const cutAt = T.indexOf(`## Section ${b}`) >= 0 ? T.indexOf(`## Section ${b}`) : T.length
          // (round 6) a note on repeated words within 200 characters of the
          // cut may wait detached; never on other words
          if (w?.state === 'detached' && T.split(n.words).length > 2 && Math.abs(want - cutAt) <= 200 + n.words.length) { waitedNear++; continue }
          if (w?.state !== 'attached' || w.start !== want) fail(`a note in section ${n.sec} (${n.words}) moved: ${JSON.stringify(w)} want ${want}`)
        }
      }
      const mine = d.notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author.id === 'sam')
      for (const n of mine) {
        if (n.where.state === 'attached' && (!draft || removed(draft.sec) || !n.replacement.includes(draft.word))) fail(`a suggestion of mine on other words: ${JSON.stringify(n.anchor.quote)} → ${JSON.stringify(n.replacement)}`)
      }
      for (const n of notes.filter(n => removed(n.sec) && n.kind === 'suggestion').slice(0, 1)) {
        const acc = await api(p, `doc/notes/${n.id}/accept`)
        if (!acc.error) fail('Accept wrote a suggestion whose words are gone')
      }
      const now = await storedWithMine(p)
      if (body(now.text) !== body(await shown(page))) fail('the editor differs from the stored text with my drafts')
      // put back: on disk, or by restoring the full version
      if (r() < 0.5 && fullId) await api(p, 'doc/restore', 'POST', { save: fullId })
      else await outside(p, full)
      await page.waitForTimeout(2200)
      const d2 = await api(p, 'doc', 'GET')
      for (const n of notes) {
        const w = d2.notes.find(x => x.id === n.id)
        if (w?.state !== 'open') continue
        checked++
        const want = n.words === 'keep it too' ? nthIn(d2.text, n.words, n.sec) : d2.text.indexOf(n.words)
        if (w.where.state !== 'attached' || w.where.start !== want) fail(`put back, a note of section ${n.sec} (${n.words}) is not home: ${JSON.stringify(w.where)} want ${want}`)
      }
    } catch (err) { fail(`threw ${err.message.split('\n')[0]}`) }
  }
  await ctx?.close()
  console.log(`# ${WHOLE} seeded runs of whole sections removed and put back (${restores} by restore, ${drafts} with a draft open), ${checked} note places checked, ${waitedNear} waited near the cut`)
  assert.deepEqual(failures, [])
  assert.deepEqual(errors, [])
})

// -- round 6: twins after a change nobody described -------------------------------

// The doc's text cut into its head and its sections, to write it back with
// some of them: twins differ only in their digits.
function sectionsOf(t) {
  const at = [1, 2, 3, 4, 5, 6].map(i => t.indexOf(`## Section ${i}`))
  const head = t.slice(0, at[0])
  const secs = {}
  at.forEach((a, k) => { secs[k + 1] = t.slice(a, k < 5 ? at[k + 1] : t.length) })
  return { head, secs, build: (on) => head + [...on].sort().map(i => secs[i]).join('') }
}
const stateOf = (page, id) => page.evaluate((id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state, id)

test('Twins 1: swapping which twin section is missing, on disk: "bright" never lands on 2b, and is home on 3b when the file is back', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const bright = await noteOn(p, 'suggestion', 'remarkable', 3, { replacement: 'bright' })
  const splendid = await noteOn(p, 'suggestion', 'remarkable', 2, { replacement: 'splendid' })
  const page = await open({ doc: p, h: 3200 })
  const full = (await disk(p)).text
  const S = sectionsOf(full)
  await outside(p, S.build([1, 3, 4, 5, 6]))
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', splendid)
  assert.equal((await whereOf(p, bright)).where.start, nthIn((await disk(p)).text, 'remarkable', 2), 'bright on 3b, the second "remarkable" now')
  await outside(p, S.build([1, 2, 4, 5, 6]))
  await page.waitForTimeout(2500)
  const wb = (await whereOf(p, bright)).where
  assert.notEqual(wb.state, 'attached', `bright is not on 2b: ${JSON.stringify(wb)}`)
  assert.equal(await drawnFor(page, bright), 0)
  await outside(p, full)
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', bright)
  const t = (await disk(p)).text
  assert.equal(t, full)
  assert.equal((await whereOf(p, bright)).where.start, nthIn(t, 'remarkable', 3), 'home on 3b')
  assert.equal((await whereOf(p, splendid)).where.start, nthIn(t, 'remarkable', 2), 'home on 2b')
  await page.waitForTimeout(300)
  await page.click(`.dc-card[data-id="${bright}"]`)
  await page.click('.dc-card.on .dc-sm.ok')
  await until(page, (id) => window.__ed.state.notes.find(x => x.id === id)?.state === 'accepted', bright)
  const after = (await disk(p)).text
  assert.ok(after.includes('Paragraph 3b, after the blocks. Nothing here is bright'), 'Accept wrote on 3b')
  assert.ok(after.includes('Paragraph 2b, after the blocks. Nothing here is remarkable'))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Twins 2: through History restores, a suggestion on 2c never sits on another "closes"; the full version brings every note home', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  await noteOn(p, 'comment', 'Walk', 1)
  const full = (await disk(p)).text
  const S = sectionsOf(full)
  const history = async () => (await (await fetch(`${base}/x/doc-comments/doc/history?${new URLSearchParams({ root: 'vault', path: p })}`)).json()).saves
  const page = await open({ doc: p, h: 3200 })
  await outside(p, S.head + 'A short page. Paragraph 9c closes the section with a sentence, and that is all.\n')
  await page.waitForTimeout(2000)
  const vTiny = (await history())[0].id
  await outside(p, S.build([1, 3, 4, 5, 6]))
  await page.waitForTimeout(2000)
  const vNo2 = (await history())[0].id
  await outside(p, full)
  await page.waitForTimeout(2000)
  const vFull = (await history())[0].id
  const ends = await noteOn(p, 'suggestion', 'closes', 2, { replacement: 'ends' })
  const others = []
  for (const i of [1, 3, 4, 5, 6]) others.push(await noteOn(p, 'comment', `Paragraph ${i}a. The river`, 1))
  await api(p, 'doc/restore', 'POST', { save: vTiny })
  await page.waitForTimeout(2000)
  assert.notEqual((await whereOf(p, ends)).where.state, 'attached', 'not on the short page\'s "closes"')
  await api(p, 'doc/restore', 'POST', { save: vNo2 })
  await page.waitForTimeout(2000)
  assert.notEqual((await whereOf(p, ends)).where.state, 'attached', 'not on 3c\'s "closes"')
  assert.equal(await drawnFor(page, ends), 0)
  const back = (await Promise.all(others.map(id => whereOf(p, id)))).filter(n => n.where.state === 'attached').length
  assert.ok(back >= 4, `the notes whose passages are on the page are back (${back} of 5)`)
  await api(p, 'doc/restore', 'POST', { save: vFull })
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', ends)
  const t = (await disk(p)).text
  assert.equal((await whereOf(p, ends)).where.start, nthIn(t, 'closes', 2))
  for (const id of others) assert.equal((await whereOf(p, id)).where.state, 'attached')
  await api(p, `doc/notes/${ends}/accept`)
  assert.ok((await disk(p)).text.includes('Paragraph 2c ends the section'))
  assert.ok((await disk(p)).text.includes('Paragraph 3c closes the section'))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('Twins 3: two twin paragraphs, the first deleted on disk: its suggestion waits, Accept writes nothing; put back, it is home', { skip }, async () => {
  await start()
  const para = 'The same paragraph, word for word, twice over in this doc.\n\n'
  const p = await newDoc('# Twins\n\n' + para + para + 'And then the end.\n')
  const id = await noteOn(p, 'suggestion', 'word for word', 1, { replacement: 'letter for letter' })
  const page = await open({ doc: p })
  const t = (await disk(p)).text
  const a = t.indexOf('The same paragraph'), b = t.indexOf('The same paragraph', a + 1)
  await outside(p, t.slice(0, a) + t.slice(b))
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'detached', id)
  assert.equal(await drawnFor(page, id), 0)
  const acc = await api(p, `doc/notes/${id}/accept`)
  assert.ok(acc.error, 'Accept refuses')
  assert.ok(!(await disk(p)).text.includes('letter for letter'))
  await outside(p, t)
  await until(page, (id) => window.__ed.state.notes.find(n => n.id === id)?.where?.state === 'attached', id)
  assert.equal((await whereOf(p, id)).where.start, a + 'The same paragraph, '.length)
  assert.deepEqual(page.errors, [])
  await page.done()
})

// Seeded: twin sections swapped in and out in single writes, removed, put
// back, a short page sharing a phrase, and History restores across them, with
// the doc open (Suggesting with a draft in some runs). An attached note is on
// its own section's words; a draft of mine is never sent on another
// section's words; the editor is the stored text with my drafts; and a text
// back byte for byte has every note where it was in it.
const TWINS = Number(process.env.DC_TWINS || 12)
test(`Twins: ${TWINS} seeded runs of twin sections swapped, removed, put back and restored, with drafts open`, { skip, timeout: 1800000 }, async () => {
  await start()
  const failures = [], errors = []
  let ctx = null, page = null, checked = 0, waited = 0, returned = 0, drafts = 0, restores = 0
  const SPOTS = ['remarkable', 'closes the section', 'The river kept its own time', 'the light']
  const history = async (p) => (await (await fetch(`${base}/x/doc-comments/doc/history?${new URLSearchParams({ root: 'vault', path: p })}`)).json()).saves
  for (let seed = Number(process.env.DC_TWIN_SEED || 1); seed <= (Number(process.env.DC_TWIN_SEED) || TWINS) && failures.length < 4; seed++) {
    if (!ctx || (seed - 1) % 6 === 0) {
      await ctx?.close()
      ctx = await browser.newContext({ viewport: { width: 1280, height: 3400 } })
      page = await ctx.newPage()
      page.on('pageerror', (e) => errors.push(`seed ${seed}: ${e.message}`))
    }
    let s = (seed * 3266489917) >>> 0
    const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 2 ** 32 }
    const pick = (n) => Math.floor(r() * n)
    const fail = (m) => failures.push(`seed ${seed}: ${m}`)
    try {
      const p = await newDoc(REPEATS)
      await noteOn(p, 'comment', 'Walk', 1)
      const S = sectionsOf((await disk(p)).text)
      const notes = {}
      for (let k = 0; k < 4; k++) {
        const sec = 1 + pick(6), ph = SPOTS[pick(SPOTS.length)]
        if (Object.values(notes).some(v => v.sec === sec && v.ph === ph)) continue
        const t = (await disk(p)).text
        const at = t.indexOf(ph, t.indexOf(`## Section ${sec}`))
        const d = await api(p, 'doc', 'GET')
        const kind = r() < 0.5 ? 'comment' : 'suggestion'
        const got = await api(p, 'doc/notes', 'POST', { kind, start: at, end: at + ph.length, quote: ph, revision: d.revision, text: 'x', ...(kind === 'suggestion' ? { replacement: ph + ' too' } : {}) }, 'ada')
        notes[got.note] = { sec, ph }
      }
      await page.goto(`${base}/?path=${encodeURIComponent(p)}`)
      await page.waitForSelector('.cm-content')
      await page.waitForTimeout(300)
      let draft = null
      if (r() < 0.5) {
        draft = { sec: 1 + pick(6), word: `zq${seed}x` }
        await mode(page, 'Suggesting')
        const at = await page.evaluate((i) => { const t = window.__ed.text(); return t.indexOf(`Paragraph ${i}a.`) + `Paragraph ${i}a. `.length }, draft.sec)
        await page.evaluate((x) => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: x } }) }, at)
        await page.keyboard.type(draft.word)
        await settle(page)
        drafts++
      }
      let on = new Set([1, 2, 3, 4, 5, 6])
      const seen = new Map()
      for (let step = 0; step < 7; step++) {
        const prev = (await disk(p)).text
        const absent = [1, 2, 3, 4, 5, 6].filter(i => !on.has(i))
        const x = r()
        let how
        if (x < 0.35 && absent.length && on.size) { const i = [...on][pick(on.size)], j = absent[pick(absent.length)]; on.delete(i); on.add(j); how = 'swap' }
        else if (x < 0.55 && on.size > 1) { on.delete([...on][pick(on.size)]); how = 'remove' }
        else if (x < 0.7 && absent.length) { on.add(absent[pick(absent.length)]); how = 'put back' }
        else if (x < 0.78) { on = null; how = 'short page' }
        else how = 'restore'
        if (how === 'restore') {
          const saves = await history(p)
          if (saves.length < 2) continue
          await api(p, 'doc/restore', 'POST', { save: saves[1 + pick(saves.length - 1)].id })
          restores++
        } else {
          await outside(p, on ? S.build(on) : S.head + 'A short page. Paragraph 9c closes the section with a sentence; the light is remarkable.\n')
        }
        await page.waitForTimeout(2200)
        await settle(page)
        const d = await api(p, 'doc', 'GET')
        const t = d.text
        on = t.includes('## Section') ? new Set([1, 2, 3, 4, 5, 6].filter(i => t.includes(`## Section ${i}`))) : new Set()
        const secAt = (x) => { let k = null; for (let i = 1; i <= 6; i++) { const a = t.indexOf(`## Section ${i}`); if (a >= 0 && a <= x) k = (k === null || a > t.indexOf(`## Section ${k}`)) ? i : k } return k }
        const was = seen.get(t)
        for (const [id, v] of Object.entries(notes)) {
          const n = d.notes.find(x => x.id === id)
          if (!n || n.state !== 'open') continue
          const w = n.where
          if (was && was[id]) {
            if (w.state !== 'attached' || w.start !== was[id]) fail(`${how}: the text came back, the note (${v.ph}) did not: ${JSON.stringify(w)} want ${was[id]}`)
            else returned++
            if (w.state === 'attached') v.sec = secAt(w.start)
            continue
          }
          if (w.state !== 'attached') { if (on.has(v.sec)) waited++; if (await drawnFor(page, id)) fail('a detached note is drawn'); continue }
          if (t.split(v.ph).length === 2 && prev.split(v.ph).length === 2) { v.sec = secAt(w.start); continue }   // the only copy
          const want = on.has(v.sec) ? t.indexOf(v.ph, t.indexOf(`## Section ${v.sec}`)) : -1
          if (w.start !== want) fail(`${how}: a note of section ${v.sec} (${v.ph}) is on other words at ${w.start} (want ${want})`)
          else checked++
        }
        for (const n of d.notes.filter(n => n.kind === 'suggestion' && n.state === 'open' && n.author.id === 'sam' && n.where.state === 'attached')) {
          if (!draft || !n.replacement.includes(draft.word)) fail(`${how}: a suggestion of mine on other words: ${JSON.stringify(n.anchor.quote)} → ${JSON.stringify(n.replacement)}`)
        }
        const now = await storedWithMine(p)
        const ed_ = await shown(page)
        if (body(now.text) !== body(ed_)) {
          const A = body(now.text), B = body(ed_)
          let k0 = 0; while (A[k0] === B[k0]) k0++
          fail(`${how}: the editor differs from the stored text with my drafts at ${k0}: ${JSON.stringify(A.slice(k0 - 30, k0 + 30))} vs ${JSON.stringify(B.slice(k0 - 30, k0 + 30))}; mine ${JSON.stringify(d.notes.filter(n => n.author.id === 'sam' && n.kind === 'suggestion').map(n => [n.state, n.where.state, n.replacement]))}`)
        }
        const places = {}
        for (const n of d.notes) if (n.state === 'open' && n.where.state === 'attached' && notes[n.id]) places[n.id] = n.where.start
        seen.set(t, { ...(was || {}), ...places })
      }
    } catch (err) { fail(`threw ${err.message.split('\n')[0]}`) }
  }
  await ctx?.close()
  console.log(`# ${TWINS} seeded runs of twin sections (${restores} restores, ${drafts} with a draft open): ${checked} attached notes on their own words, ${waited} waited, ${returned} back in an earlier text`)
  assert.deepEqual(failures, [])
  assert.deepEqual(errors, [])
})

// Round 6, B1: at 430 a 24-character title is whole on both bars, and the
// owner's phone bar names the doc, not its folders.
async function barAt(page) {
  return page.evaluate(() => {
    const head = document.querySelector('.dc-head')
    const crumb = head.querySelector('.dc-crumb')
    const b = crumb.querySelector('b') || crumb.lastElementChild || crumb
    const kids = [...head.children].filter(e => e.offsetWidth > 0)
    const next = kids[kids.indexOf(crumb) + 1]
    return {
      text: crumb.innerText.replace(/\s+/g, ' ').trim(), title: b.textContent,
      box: Math.round(b.getBoundingClientRect().width), needs: b.scrollWidth, clipped: b.scrollWidth > b.clientWidth + 0.5,
      free: next ? Math.round(next.getBoundingClientRect().left - b.getBoundingClientRect().right) : null,
      padding: getComputedStyle(head).padding,
    }
  })
}
test('B1 (round 6): at 430, a 24-character title is whole on the visitor\'s bar and the owner\'s, and the owner\'s names the doc', { skip }, async () => {
  await start()
  const p = await newDoc('# Notes from the allotment\n\nThe beans came up in a row, then the frost took half of them.\n')
  const link = await devLink('comment', 'Robin', p)
  const v = await visitor(link, { w: 430, h: 900 })
  await v.waitForTimeout(600)
  const mv = await barAt(v)
  await shot(v, 'phone-bar-visitor-24')
  await v.done()
  const o = await open({ doc: p, w: 430, h: 900, phone: true })
  const mo = await barAt(o)
  await shot(o, 'phone-bar-owner-24')
  await o.done()
  // a name that needs the room, measured again after the page draws itself
  // again (an answer, a scroll): the bar keeps it whole
  const long = `Tests/Allotment notes ${process.pid}.md`
  await outside(long, '# Allotment\n\nRows of beans.\n')
  const o2 = await open({ doc: long, w: 430, h: 900, phone: true })
  await o2.evaluate(() => window.__ed.reload())
  await o2.waitForTimeout(600)
  // Loam asks for the bar again on each of its own re-renders
  await o2.evaluate(() => { for (let i = 0; i < 3; i++) window.__ed.refreshHead() })
  await o2.waitForTimeout(200)
  await o2.evaluate(() => { const s = document.querySelector('.dc-scroll'); s.scrollTop = 40; s.dispatchEvent(new Event('scroll')) })
  await o2.waitForTimeout(300)
  const mo2 = await barAt(o2)
  mo2.head = await o2.evaluate(() => [document.querySelector('.dc-head').className, document.querySelector('.dc').className, Math.round(document.querySelector('.dc-mpill').getBoundingClientRect().width)])
  await o2.done()
  console.log('# owner, a longer name, after drawing again', JSON.stringify(mo2))
  assert.ok(!mo2.clipped, `owner: "${mo2.title}" needs ${mo2.needs}, has ${mo2.box}`)
  console.log('# visitor', JSON.stringify(mv), '\n# owner', JSON.stringify(mo))
  assert.equal(mv.title, 'Notes from the allotment')
  assert.ok(!mv.clipped, `visitor: the title needs ${mv.needs}, has ${mv.box}`)
  assert.equal(mo.text, p.replace(/^Tests\//, '').replace(/\.md$/, ''), 'owner: the bar names the doc, not its folders')
  assert.ok(!mo.clipped, `owner: the name needs ${mo.needs}, has ${mo.box}`)
})

test('B2 (round 6): in Suggesting, with my draft\'s card focused, a click on a table cell\'s mark focuses that note', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'comment', 'Porto', 1)
  const page = await open({ doc: p, h: 2000 })
  await mode(page, 'Suggesting')
  await caretAfter(page, 'Paragraph 1a.')
  await page.keyboard.type(' Quietly')
  await settle(page)
  await page.waitForTimeout(300)
  const mine = (await mineOpen(p))[0]
  await page.click(`.dc-card[data-id="${mine.id}"]`)
  await until(page, (x) => window.__ed.state.focus === x, mine.id)
  // her own words selected, as after working on the draft
  const t0 = await shown(page)
  await selectText(page, t0.indexOf('Quietly'), t0.indexOf('Quietly') + 7)
  await page.click(`.dc-table [data-note="${id}"]`)
  await page.waitForTimeout(300)
  assert.equal(await page.evaluate(() => window.__ed.state.focus), id)
  assert.equal(await page.locator('.dc-table').count() > 0, true, 'the table is still drawn, not opened as source')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B2 (round 6), the visitor\'s page: with her draft\'s card focused, a click on a table cell\'s mark focuses that note', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const id = await noteOn(p, 'comment', 'Porto', 1)
  const link = await devLink('comment', 'Robin', p)
  const page = await visitor(link, { h: 2000 })
  await mode(page, 'Suggesting')
  await page.evaluate(() => { const v = document.querySelector('.cm-content').cmTile.view, at = v.state.doc.toString().indexOf('Paragraph 1a.') + 13; v.focus(); v.dispatch({ selection: { anchor: at } }) })
  await page.keyboard.type(' Quietly')
  await page.waitForTimeout(PAUSE + 600)
  const mine = (await api(p, 'doc', 'GET')).notes.find(n => n.kind === 'suggestion' && n.state === 'open')
  await page.click(`.dc-card[data-id="${mine.id}"]`)
  await page.waitForTimeout(300)
  await page.click(`.dc-table [data-note="${id}"]`)
  await page.waitForTimeout(400)
  const focused = await page.evaluate(() => document.querySelector('.dc-card.on')?.getAttribute('data-id'))
  assert.equal(focused, id)
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B3 (round 6): a note on two words of a table cell marks those words, not the cell', { skip }, async () => {
  await start()
  const p = await newDoc('# Rows\n\n| Year | Where | Why |\n| --- | --- | --- |\n| 2016 | Lisbon | the light on the river |\n')
  const id = await noteOn(p, 'comment', 'the river', 1)
  const page = await open({ doc: p })
  await page.waitForTimeout(300)
  const got = await page.evaluate((id) => { const e = document.querySelector(`.dc-table [data-note="${id}"]`); return { mark: e?.textContent, cell: e?.closest('td')?.textContent } }, id)
  assert.deepEqual(got, { mark: 'the river', cell: 'the light on the river' })
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B4 (round 6): the "words just removed" line goes when the passage comes back, and the draft is back in the editor', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const page = await open({ doc: p, h: 3200 })
  await mode(page, 'Suggesting')
  await caretAfter(page, 'Paragraph 2a.')
  await page.keyboard.type(' Slowly')
  await settle(page)
  const full = (await disk(p)).text
  const S = sectionsOf(full)
  await outside(p, S.build([1, 3, 4, 5, 6]))
  await until(page, () => document.querySelector('.dc-toast.on')?.textContent.includes('removed from the file'), null, 6000)
  assert.ok(!(await shown(page)).includes('Slowly'))
  await outside(p, full)
  await until(page, () => window.__ed.text().includes('Paragraph 2a. Slowly'), null, 6000)
  assert.equal(await page.evaluate(() => document.querySelector('.dc-toast')?.classList.contains('on')), false, 'the line has gone')
  assert.equal(body((await storedWithMine(p)).text), body(await shown(page)))
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B5 (round 6): a wrapped list item\'s second line starts under its first line\'s words (bullets and numbers)', { skip }, async () => {
  await start()
  const long = 'The first winter I stayed, nothing happened, and that was the point. The baker learned my order and the man with the grey dog started nodding.'
  const p = await newDoc(`# Lists\n\nA paragraph first.\n\n- ${long}\n\n1. ${long}\n`)
  const page = await open({ doc: p, w: 900 })
  await page.evaluate(() => { const v = window.__ed.view; v.dispatch({ selection: { anchor: 2 } }) })
  await page.waitForTimeout(300)
  const gaps = await page.evaluate((long) => {
    const v = window.__ed.view, t = v.state.doc.toString()
    return ['- ', '1. '].map(m => {
      const a = t.indexOf(m + long) + m.length
      const first = v.coordsAtPos(a, 1)
      let k = a + 1
      while (k < a + long.length && Math.abs(v.coordsAtPos(k, 1).top - first.top) < 4) k++
      const second = v.coordsAtPos(k, 1)
      return Math.round((second.left - first.left) * 10) / 10
    })
  }, long)
  console.log('# second line minus first line, px:', JSON.stringify(gaps))
  for (const g of gaps) assert.ok(Math.abs(g) <= 1, `the wrapped line is ${g}px off`)
  await shot(page, 'list-wrap')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('B5 (round 6): the Share button\'s faces sit apart, each ring in the button\'s colour', { skip }, async () => {
  await start()
  const p = await newDoc('# Shared\n\nA line.\n')
  await devLink('comment', 'Robin', p)
  await devLink('comment', null, p)
  const page = await open({ doc: p })
  await page.waitForSelector('.dc-sharebtn.has .dc-sf')
  const m = await page.evaluate(() => {
    const f = [...document.querySelectorAll('.dc-sharebtn .dc-sf')].map(e => e.getBoundingClientRect())
    const btn = getComputedStyle(document.querySelector('.dc-sharebtn')).backgroundColor
    const ring = getComputedStyle(document.querySelector('.dc-sharebtn .dc-sf')).boxShadow
    return { n: f.length, overlap: f.length > 1 ? Math.round(f[0].right - f[1].left) : 0, btn, ring }
  })
  console.log('# share faces', JSON.stringify(m))
  assert.equal(m.n, 2)
  assert.ok(m.overlap <= 3, `faces overlap ${m.overlap}px`)
  assert.ok(m.ring.includes(m.btn), 'the ring is the button\'s colour')
  await page.screenshot({ path: `${SHOTS || '/tmp'}/share-faces.png`, clip: await page.evaluate(() => { const r = document.querySelector('.dc-sharebtn').getBoundingClientRect(); return { x: r.x - 20, y: r.y - 10, width: r.width + 40, height: r.height + 20 } }) }).catch(() => {})
  await page.done()
})

// -- the share sheet is a bottom sheet on a phone (2026-11-17) ---------------------

// A finger's drag, by the browser's own touch events.
async function drag(page, x, y0, y1, steps = 8) {
  const cdp = await page.context().newCDPSession(page)
  const at = (y) => [{ x, y, id: 1 }]
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: at(y0) })
  for (let i = 1; i <= steps; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: at(y0 + ((y1 - y0) * i) / steps) })
    await page.waitForTimeout(16)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
}

const sheetBox = (page) => page.evaluate(() => {
  const d = document.querySelector('.dc-share-scrim .dc-dlg')
  if (!d) return null
  const r = d.getBoundingClientRect()
  const g = d.querySelector('.dc-bs-grab')
  return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, w: innerWidth, h: innerHeight, grab: g ? getComputedStyle(g).display : null, radius: getComputedStyle(d).borderTopLeftRadius, bl: getComputedStyle(d).borderBottomLeftRadius, overflow: getComputedStyle(d).overflowY }
})

for (const [w, dark] of [[430, true], [375, false]]) {
  test(`the share sheet on a ${w}-wide phone (${dark ? 'dark' : 'light'}): it rises from the foot with a handle, and a drag down or a tap above closes it`, { skip }, async () => {
    await start()
    const p = await newDoc('# Shared\n\nA line.\n')
    await devLink('comment', 'Robin', p)
    const ctx = await browser.newContext({ viewport: { width: w, height: 800 }, hasTouch: true, isMobile: true, colorScheme: dark ? 'dark' : 'light' })
    const page = await ctx.newPage()
    const errors = []
    page.on('pageerror', (e) => errors.push(e.message))
    await page.goto(`${base}/?phone${dark ? '&dark' : ''}&path=${encodeURIComponent(p)}`)
    await page.waitForSelector('.dc-sharebtn.has')
    await page.click('.dc-sharebtn')
    await page.waitForSelector('.dc-share-scrim.on .dc-sw-row')
    await page.waitForTimeout(450)
    const b = await sheetBox(page)
    console.log('# share sheet', w, JSON.stringify(b))
    assert.ok(Math.abs(b.left) < 1 && Math.abs(b.right - b.w) < 1, 'edge to edge')
    assert.ok(Math.abs(b.bottom - b.h) < 1, 'standing on the foot of the screen')
    assert.equal(b.grab, 'block', 'the handle shows')
    assert.equal(b.bl, '0px', 'square at the foot, round at the top')
    assert.notEqual(b.radius, '0px')
    assert.equal(b.overflow, 'auto', 'it scrolls inside when tall')
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `share-sheet-list-${w}-${dark ? 'dark' : 'light'}.png`) })
    // A row's ⋯ menu on a short sheet opens upward, whole, over the page.
    await page.click('.dc-sw-row .dc-ddw:last-child .dc-ib')
    await page.waitForSelector('.dc-rowmenu')
    await page.waitForTimeout(250)
    const m = await page.evaluate(() => {
      const r = document.querySelector('.dc-rowmenu').getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + 12)
      return { top: r.top, bottom: r.bottom, h: innerHeight, up: document.querySelector('.dc-rowmenu').classList.contains('dc-up'), seen: Boolean(hit?.closest('.dc-rowmenu')) }
    })
    assert.ok(m.up && m.top >= 0 && m.bottom <= m.h && m.seen, `the menu is whole and in view (${JSON.stringify(m)})`)
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `share-sheet-menu-${w}-${dark ? 'dark' : 'light'}.png`) })
    await page.keyboard.press('Escape')
    await until(page, () => !document.querySelector('.dc-rowmenu'), null, 2000)
    // The invite card, then a mode menu near its top: it opens down, inside.
    await page.click('.dc-sh-go')
    await page.waitForSelector('.dc-share-scrim .dc-ticket')
    await page.waitForTimeout(300)
    const c = await sheetBox(page)
    assert.ok(Math.abs(c.bottom - c.h) < 1 && c.top >= 0, `the invite card fits as a sheet (${JSON.stringify(c)})`)
    const fs = await page.evaluate(() => getComputedStyle(document.querySelector('.dc-iv-name')).fontSize)
    assert.equal(fs, '16px', 'the name field never zooms the page')
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `share-sheet-invite-${w}-${dark ? 'dark' : 'light'}.png`) })
    // A small drag settles back; a long one closes it.
    await page.evaluate(() => document.activeElement?.blur())
    await drag(page, w / 2, c.top + 10, c.top + 40)
    await page.waitForTimeout(450)
    assert.ok(Math.abs((await sheetBox(page)).top - c.top) < 1, 'a short drag settles back')
    await drag(page, w / 2, c.top + 10, c.top + 260)
    await until(page, () => !document.querySelector('.dc-share-scrim'), null, 2000)
    // Opened again, a tap above it closes it.
    await page.click('.dc-sharebtn')
    await page.waitForSelector('.dc-share-scrim.on .dc-sw-row')
    await page.waitForTimeout(400)
    await page.touchscreen.tap(w / 2, 12)
    await until(page, () => !document.querySelector('.dc-share-scrim'), null, 2000)
    assert.deepEqual(errors, [])
    await ctx.close()
  })
}

test('the share sheet on a phone stands on the keyboard: the screen left above it is its whole height', { skip }, async () => {
  await start()
  const p = await newDoc('# Shared\n\nA line.\n')
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const page = await ctx.newPage()
  // A keyboard, as the page sees one: the visual viewport shrinks from the foot.
  await page.addInitScript(() => {
    const vv = new EventTarget()
    let height = innerHeight
    Object.defineProperty(vv, 'height', { get: () => height })
    Object.defineProperty(vv, 'offsetTop', { get: () => 0 })
    Object.defineProperty(vv, 'width', { get: () => innerWidth })
    window.__keyboard = (px) => { height = innerHeight - px; vv.dispatchEvent(new Event('resize')) }
    Object.defineProperty(window, 'visualViewport', { get: () => vv })
  })
  await page.goto(`${base}/?phone&path=${encodeURIComponent(p)}`)
  await page.waitForSelector('.dc-sharebtn')
  await page.click('.dc-sharebtn')
  await page.waitForSelector('.dc-share-scrim.on .dc-iv-name')
  await page.waitForTimeout(400)
  await page.evaluate(() => window.__keyboard(336))
  await page.waitForTimeout(100)
  const m = await page.evaluate(() => {
    const d = document.querySelector('.dc-share-scrim .dc-dlg').getBoundingClientRect()
    const f = document.querySelector('.dc-iv-name').getBoundingClientRect()
    return { bottom: d.bottom, top: d.top, field: f.bottom, line: innerHeight - 336, scrolls: document.querySelector('.dc-share-scrim .dc-dlg').scrollHeight > document.querySelector('.dc-share-scrim .dc-dlg').clientHeight }
  })
  console.log('# keyboard', JSON.stringify(m))
  assert.ok(Math.abs(m.bottom - m.line) < 1, `the sheet's foot is on the keyboard (${JSON.stringify(m)})`)
  assert.ok(m.top >= 0 && m.field <= m.line, 'the field being typed in is above the keyboard')
  assert.ok(m.scrolls, 'the rest scrolls inside')
  await page.evaluate(() => window.__keyboard(0))
  await page.waitForTimeout(100)
  assert.ok(Math.abs((await page.evaluate(() => document.querySelector('.dc-share-scrim .dc-dlg').getBoundingClientRect().bottom)) - 844) < 1, 'and back on the foot when it goes')
  await ctx.close()
})

test('the share sheet on a desktop stays the centred card, with no handle', { skip }, async () => {
  await start()
  const p = await newDoc('# Shared\n\nA line.\n')
  await devLink('comment', 'Robin', p)
  const page = await open({ doc: p })
  await page.click('.dc-sharebtn')
  await page.waitForSelector('.dc-share-scrim.on .dc-sw-row')
  await page.waitForTimeout(400)
  const b = await sheetBox(page)
  assert.equal(b.grab, 'none')
  assert.ok(Math.abs((b.left + b.right) / 2 - b.w / 2) < 2 && b.right - b.left <= 460, `centred, at most 460 wide (${JSON.stringify(b)})`)
  assert.ok(Math.abs((b.top + b.bottom) / 2 - b.h / 2) < 2, 'centred up and down')
  assert.notEqual(b.bl, '0px')
  await shot(page, 'share-sheet-desktop')
  assert.deepEqual(page.errors, [])
  await page.done()
})

// -- the selection popup waits until the selecting stops -------------------------

// Every change of the popup's classes, timed, from the page itself: what the
// eye would see between the test's own looks.
async function watchSelbar(page) {
  await page.evaluate(() => {
    const bar = document.querySelector('.dc-selbar')
    window.__sel = { log: [], t0: performance.now() }
    const note = () => window.__sel.log.push({ t: performance.now(), on: bar.classList.contains('on'), out: bar.classList.contains('out'), left: bar.style.left, top: bar.style.top })
    new MutationObserver(note).observe(bar, { attributes: true, attributeFilter: ['class', 'style'] })
  })
}
const selbarState = (page) => page.evaluate(() => {
  const bar = document.querySelector('.dc-selbar')
  const cs = getComputedStyle(bar)
  return { on: bar.classList.contains('on'), seen: cs.display !== 'none' && Number(cs.opacity) > 0.02 && !bar.classList.contains('out'), r: bar.getBoundingClientRect().toJSON() }
})
const shownSince = (page, t) => page.evaluate((t) => window.__sel.log.filter(e => e.t >= t && e.on).length, t)
const now = (page) => page.evaluate(() => performance.now())
// The screen point of a character, from the editor's own measure.
const pointAt = (page, pos, side = 1) => page.evaluate(([p, s]) => { const c = window.__ed.view.coordsAtPos(p, s); return { x: s > 0 ? c.left + 1 : c.right - 1, y: (c.top + c.bottom) / 2 } }, [pos, side])

for (const [w, h, label] of [[1280, 850, 'desktop'], [430, 900, 'narrow']]) {
  test(`a real drag: no popup on any step of it, forwards, backwards, paused; it comes after the release, above the words, not under the pointer (${label})`, { skip }, async () => {
    await start()
    const p = await newDoc(REPEATS)
    const page = await open({ doc: p, w, h })
    await watchSelbar(page)
    const t = await page.evaluate(() => window.__ed.text())
    const from = t.indexOf('The river kept'), to = t.indexOf('Paragraph 1b')
    const a = await pointAt(page, from), b = await pointAt(page, from + 'The river kept its own time'.length, -1)
    // Forwards across the line, a long pause to read, backwards, on again.
    await page.mouse.move(a.x, a.y)
    await page.mouse.down()
    const t0 = await now(page)
    const steps = []
    for (let i = 1; i <= 24; i++) steps.push({ x: a.x + (b.x - a.x) * i / 24, y: a.y + (b.y - a.y) * i / 24 })
    for (let i = 23; i >= 10; i--) steps.push(steps[i - 1])
    for (let i = 10; i <= 24; i++) steps.push(steps[i - 1])
    let n = 0
    for (const s of steps) {
      await page.mouse.move(s.x, s.y)
      await page.waitForTimeout(++n === 24 ? 700 : 25)
      const st = await selbarState(page)
      assert.equal(st.seen, false, `no popup on step ${n} of the drag`)
    }
    // Down the page: over the callout and the table, into the next paragraph.
    const c = await pointAt(page, to + 4, -1)
    for (let i = 1; i <= 12; i++) {
      await page.mouse.move(b.x + (c.x - b.x) * i / 12, b.y + (c.y - b.y) * i / 12)
      await page.waitForTimeout(30)
      assert.equal((await selbarState(page)).seen, false, `no popup crossing the callout and table, step ${i}`)
    }
    assert.ok(await page.evaluate(() => !window.__ed.view.state.selection.main.empty), 'the drag made a selection')
    assert.equal(await shownSince(page, t0), 0, 'never shown during the drag, by the page\'s own record')
    await shot(page, `drag-mid-${label}`)
    // Back to a one-line selection, then let go.
    await page.mouse.move(b.x, b.y)
    await page.waitForTimeout(40)
    const up = await now(page)
    await page.mouse.up()
    assert.equal((await selbarState(page)).seen, false, 'not at the instant of release')
    await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
    const first = await page.evaluate((up) => window.__sel.log.find(e => e.t >= up && e.on).t - up, up)
    assert.ok(first >= 90 && first <= 400, `it comes a short beat after the release (${first.toFixed(0)} ms)`)
    await page.waitForTimeout(260)
    const st = await selbarState(page)
    assert.equal(st.seen, true)
    const sel = await page.evaluate(() => { const v = window.__ed.view, r = v.state.selection.main; return v.coordsAtPos(r.from, 1).top })
    assert.ok(st.r.bottom <= sel + 1 && sel - st.r.bottom <= 16, `above the selection (${(sel - st.r.bottom).toFixed(1)} px)`)
    const inside = b.x >= st.r.left - 2 && b.x <= st.r.right + 2 && b.y >= st.r.top - 2 && b.y <= st.r.bottom + 2
    assert.equal(inside, false, 'not under the pointer')
    // Shown once, in one place: no move after it came.
    const moves = await page.evaluate((up) => new Set(window.__sel.log.filter(e => e.t >= up && e.on).map(e => `${e.left},${e.top}`)).size, up)
    assert.equal(moves, 1, 'it does not move once it has come')
    assert.equal(await page.evaluate(() => document.activeElement?.closest?.('.cm-content') != null), true, 'focus stays in the text')
    await shot(page, `drag-released-${label}`)
    // A new drag sends it away at once.
    await page.mouse.move(a.x, a.y + 2)
    await page.mouse.down()
    const gone = await selbarState(page)
    assert.equal(gone.seen, false, 'gone the moment a new selection starts')
    assert.equal(await page.evaluate(() => { const b = document.querySelector('.dc-selbar'); return getComputedStyle(b).display === 'none' || getComputedStyle(b).pointerEvents === 'none' }), true, 'its fade is out of the pointer\'s way')
    await page.mouse.up()
    assert.deepEqual(page.errors, [])
    await page.done()
  })
}

test('a double-click and a triple-click are one gesture: shown once, for the paragraph', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const page = await open({ doc: p })
  await watchSelbar(page)
  const t = await page.evaluate(() => window.__ed.text())
  const at = await pointAt(page, t.indexOf('river kept') + 2)
  const t0 = await now(page)
  // A person's triple-click: the third press comes ~180 ms after the second.
  await page.mouse.click(at.x, at.y, { clickCount: 1 })
  await page.waitForTimeout(90)
  await page.mouse.click(at.x, at.y, { clickCount: 2 })
  await page.waitForTimeout(180)
  assert.equal(await shownSince(page, t0), 0, 'not shown for the word while a third click may come')
  await page.mouse.click(at.x, at.y, { clickCount: 3 })
  await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
  await page.waitForTimeout(300)
  const shows = await page.evaluate((t0) => { let n = 0, was = false; for (const e of window.__sel.log) { if (e.t < t0) continue; if (e.on && !was) n++; was = e.on } return n }, t0)
  assert.equal(shows, 1, 'it came once')
  const sel = await page.evaluate(() => window.__ed.view.state.sliceDoc(window.__ed.view.state.selection.main.from, window.__ed.view.state.selection.main.to))
  assert.match(sel, /^Paragraph 1a\. The river kept/, 'the paragraph is chosen')
  // A double-click alone: shown for the word, once the sequence is over.
  await page.mouse.click(10, 10)
  await page.waitForTimeout(600)
  const t1 = await now(page)
  const w = await pointAt(page, t.indexOf('learned') + 2)
  await page.mouse.dblclick(w.x, w.y)
  await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
  const after = await page.evaluate((t1) => window.__sel.log.find(e => e.t >= t1 && e.on).t - t1, t1)
  assert.ok(after >= 250, `after a double-click it waits for a third (${after.toFixed(0)} ms)`)
  assert.equal(await page.evaluate(() => { const s = window.__ed.view.state.selection.main; return window.__ed.view.state.sliceDoc(s.from, s.to) }), 'learned')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('keyboard selection: nothing while Shift+arrows move, it comes once the selection is still; Select All too; typing and Shift+click send it away', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const page = await open({ doc: p })
  await watchSelbar(page)
  const t = await page.evaluate(() => window.__ed.text())
  const at = await pointAt(page, t.indexOf('The river kept'))
  await page.mouse.click(at.x, at.y)
  await page.waitForTimeout(400)
  const t0 = await now(page)
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press('Shift+ArrowRight')
    await page.waitForTimeout(70)
    assert.equal((await selbarState(page)).seen, false, `no popup while Shift+→ is pressed (${i + 1})`)
  }
  let last = 0
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Shift+Control+ArrowRight')
    last = await now(page)
    await page.waitForTimeout(120)
    assert.equal((await selbarState(page)).seen, false, 'nor by words')
  }
  assert.equal(await shownSince(page, t0), 0, 'never shown while the keys moved it')
  await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
  const after = await page.evaluate((last) => window.__sel.log.find(e => e.t >= last && e.on).t - last, last)
  assert.ok(after >= 300 && after <= 700, `once still for a moment (${after.toFixed(0)} ms)`)
  await page.waitForTimeout(250)
  assert.equal((await selbarState(page)).seen, true)
  // Shift+click extends it: gone at once, back in the new place.
  const before = (await selbarState(page)).r
  const further = await pointAt(page, t.indexOf('Paragraph 1b') + 9, -1)
  await page.keyboard.down('Shift')
  await page.mouse.move(further.x, further.y)
  await page.mouse.down()
  assert.equal((await selbarState(page)).seen, false, 'Shift+click: gone at once')
  await page.mouse.up()
  await page.keyboard.up('Shift')
  await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
  await page.waitForTimeout(250)
  const moved = (await selbarState(page)).r
  const sel2 = await page.evaluate(() => { const v = window.__ed.view, r = v.state.selection.main; return { len: r.to - r.from, top: v.coordsAtPos(r.from, 1).top } })
  assert.ok(sel2.len > 30, 'Shift+click extended the selection')
  assert.ok(moved.bottom <= sel2.top + 1 && sel2.top - moved.bottom <= 16, 'placed for the new selection')
  assert.ok(before.width > 0)
  // Select All: once still.
  await page.mouse.click(at.x, at.y)
  await page.waitForTimeout(300)
  const t2 = await now(page)
  await page.keyboard.press('Control+a')
  await page.waitForTimeout(150)
  assert.equal(await shownSince(page, t2), 0, 'not at once after Select All')
  await page.waitForTimeout(500)
  // (Select All may run past the screen: shown when any of it is in view.)
  assert.equal(await shownSince(page, t2) > 0, true, 'Select All brings it')
  // Typing sends it away at once.
  await page.keyboard.press('ArrowLeft')
  await page.waitForTimeout(50)
  assert.equal((await selbarState(page)).seen, false, 'a caret: gone')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('the popup goes when its words scroll out of sight and comes back with them; the formatting buttons keep it in place', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const page = await open({ doc: p })
  const t = await page.evaluate(() => window.__ed.text())
  const s = t.indexOf('The river kept')
  const a = await pointAt(page, s), b = await pointAt(page, s + 9, -1)
  await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move(b.x, b.y, { steps: 6 }); await page.mouse.up()
  await page.waitForSelector('.dc-selbar.on')
  await page.waitForTimeout(250)
  const place = (await selbarState(page)).r
  await watchSelbar(page)
  const t0 = await now(page)
  await page.click('.dc-selbar.on .dc-fb[aria-label="Bold"]')
  await page.waitForTimeout(250)
  assert.equal((await selbarState(page)).seen, true, 'Bold keeps it')
  const shows = await page.evaluate((t0) => window.__sel.log.filter(e => e.t >= t0 && !e.on).length, t0)
  assert.equal(shows, 0, 'no blink on Bold')
  assert.ok(Math.abs((await selbarState(page)).r.top - place.top) < 2)
  await page.evaluate(() => { document.querySelector('.dc-scroll').scrollTop += 1400 })
  await page.waitForTimeout(150)
  assert.equal((await selbarState(page)).seen, false, 'scrolled out of sight: gone')
  await page.evaluate(() => { document.querySelector('.dc-scroll').scrollTop = 0 })
  await page.waitForTimeout(300)
  assert.equal((await selbarState(page)).seen, true, 'and back with its words')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('reduced motion: the popup fades in without the movement', { skip }, async () => {
  await start()
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 850 }, reducedMotion: 'reduce' })
  const page = await ctx.newPage()
  await page.goto(`${base}/?dark&path=${encodeURIComponent('Essays/drafts/Long.md')}`)
  await page.waitForSelector('.cm-content')
  await page.waitForTimeout(400)
  const t = await page.evaluate(() => window.__ed.text())
  const s = t.indexOf('# A long doc') + 2
  const a = await pointAt(page, s), b = await pointAt(page, s + 6, -1)
  await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move(b.x, b.y, { steps: 4 }); await page.mouse.up()
  await page.waitForSelector('.dc-selbar.in', { timeout: 1500 })
  const anim = await page.evaluate(() => { const cs = getComputedStyle(document.querySelector('.dc-selbar')); return { name: cs.animationName, d: cs.animationDuration } })
  assert.equal(anim.name, 'dc-fade', 'a plain fade')
  assert.ok(parseFloat(anim.d) >= 0.1, `a fade you can see (${anim.d})`)
  await ctx.close()
})

test('phone: the bars offer Comment and Suggest once the fingers are up and the handles still; moving handles do not flicker them', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const ctx = await browser.newContext({ viewport: { width: 430, height: 900 }, hasTouch: true, isMobile: true })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(`${base}/?dark&phone&path=${encodeURIComponent(p)}`)
  await page.waitForSelector('.cm-content')
  await page.waitForTimeout(500)
  await page.evaluate(() => window.__ed.view.focus())
  await page.waitForTimeout(200)
  const cdp = await ctx.newCDPSession(page)
  const kc = () => page.evaluate(() => { const b = [...document.querySelectorAll('.dc-kbar.on .dc-kc')]; return { comment: b.some(x => /Comment/.test(x.textContent) && !x.disabled), suggest: b.some(x => /Suggest/.test(x.textContent)) } })
  const t = await page.evaluate(() => window.__ed.text())
  const s = t.indexOf('The river kept')
  // Real touches, held on the text (a long press does not become a click).
  const spot = await pointAt(page, s + 2)
  const touch = (type) => cdp.send('Input.dispatchTouchEvent', type === 'touchstart' ? { type: 'touchStart', touchPoints: [{ x: spot.x, y: spot.y }] } : { type: 'touchEnd', touchPoints: [] })
  // A long press and handles dragged: fingers down the whole time.
  await touch('touchstart')
  for (let i = 3; i <= 30; i += 3) {
    await page.evaluate(([a, b]) => window.__ed.view.dispatch({ selection: { anchor: a, head: b }, userEvent: 'select' }), [s, s + i])
    await page.waitForTimeout(60)
    assert.equal((await kc()).comment, false, `not offered while the handles move (${i})`)
  }
  await page.waitForTimeout(500)
  assert.equal((await kc()).comment, false, 'nor while the finger rests')
  await touch('touchend')
  await page.waitForTimeout(150)
  assert.equal((await kc()).comment, false, 'not at the instant the finger lifts')
  await page.waitForTimeout(400)
  assert.deepEqual(await kc(), { comment: true, suggest: true }, 'offered once still')
  await shot(page, 'phone-bar-settled')
  // A handle moved again: the bar stays as it is, no blink.
  await touch('touchstart')
  await page.evaluate(([a, b]) => window.__ed.view.dispatch({ selection: { anchor: a, head: b }, userEvent: 'select' }), [s, s + 40])
  await page.waitForTimeout(60)
  assert.equal((await kc()).comment, true, 'extending keeps the bar')
  await touch('touchend')
  await page.waitForTimeout(500)
  // A caret: back at once.
  await page.evaluate(([a]) => window.__ed.view.dispatch({ selection: { anchor: a }, userEvent: 'select' }), [s])
  await page.waitForTimeout(30)
  assert.equal((await kc()).comment, false, 'a caret: gone at once')
  assert.deepEqual(errors, [])
  await ctx.close()
})

test('a visitor\'s page (commenting link): the popup waits for the release there too, and offers Comment', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const link = await devLink('comment', 'Robin', p)
  const page = await visitor(link)
  await page.waitForTimeout(500)
  await watchSelbar(page)
  const v = (fn, arg) => page.evaluate(([f, a]) => { const view = document.querySelector('.cm-content').cmTile.view; return new Function('view', 'a', f)(view, a) }, [fn, arg])
  const s = await v('return view.state.doc.toString().indexOf("The river kept")')
  const pt = (pos, side) => v('const c = view.coordsAtPos(a[0], a[1]); return { x: a[1] > 0 ? c.left + 1 : c.right - 1, y: (c.top + c.bottom) / 2 }', [pos, side])
  const a = await pt(s, 1), b = await pt(s + 'The river kept its own time'.length, -1)
  await page.mouse.move(a.x, a.y)
  await page.mouse.down()
  const t0 = await now(page)
  for (let i = 1; i <= 12; i++) {
    await page.mouse.move(a.x + (b.x - a.x) * i / 12, a.y + (b.y - a.y) * i / 12)
    await page.waitForTimeout(30)
    assert.equal((await selbarState(page)).seen, false, `no popup on step ${i}`)
  }
  assert.equal(await shownSince(page, t0), 0)
  await page.mouse.up()
  await page.waitForSelector('.dc-selbar.on', { timeout: 1500 })
  const labels = await page.$$eval('.dc-selbar.on button', els => els.map(e => e.textContent.trim()).filter(Boolean))
  assert.ok(labels.some(l => l.startsWith('Comment')), labels.join(' | '))
  assert.deepEqual(page.errors, [])
  await page.done()
})

// Dictation into the doc only while the doc takes typed text from this
// person (2026-11-17): the mic is in the header (and the phone's keyboard
// bar) in Editing and Suggesting, and gone in Viewing, on a doc that cannot
// be written, and on a view link's page; read aloud stays in every mode.
// Switching mode adds or removes it at once, and a take running when the
// doc goes to Viewing stops with nothing more written.
const headHas = (page) => page.evaluate(() => {
  const head = document.querySelector('.dc-head')
  const mic = head.querySelector('.mic'), ra = head.querySelector('.read-aloud')
  return {
    mic: Boolean(mic), read: Boolean(ra),
    // The header closes up: what follows read aloud is the mic, or the mode
    // control itself, never an empty slot.
    next: ra?.nextElementSibling?.classList.contains('mic') ? 'mic' : ra?.nextElementSibling?.querySelector('.dc-mpill') || ra?.nextElementSibling?.classList.contains('dc-mpill') ? 'mode' : String(ra?.nextElementSibling?.className),
    anyMic: document.querySelectorAll('.dc .mic').length,
  }
})
async function pickMode(page, label) {
  await page.click('.dc-mpill')
  await page.click(`.dc-mi[role=menuitemradio]:has-text("${label}")`)
  await page.waitForTimeout(250)
}

test('dictation: the mic in Editing and Suggesting, gone in Viewing; read aloud in all three; a take stops on the way to Viewing', { skip }, async () => {
  await start()
  const p = await newDoc('# Dictated\n\nA first line.\n')
  const page = await open({ doc: p, extra: '&mic' })
  await page.waitForSelector('.dc-head .read-aloud')
  assert.deepEqual(await headHas(page), { mic: true, read: true, next: 'mic', anyMic: 1 }, 'Editing')
  await shot(page, 'mic-editing')

  await pickMode(page, 'Suggesting')
  assert.deepEqual(await headHas(page), { mic: true, read: true, next: 'mic', anyMic: 1 }, 'Suggesting')
  // A take in Suggesting lands as a suggestion, as typed words do.
  await page.click('.dc-head .mic')
  await page.evaluate(() => window.__dictate(' Said aloud.'))
  await page.waitForTimeout(200)
  assert.ok((await page.evaluate(() => window.__ed.text())).includes('Said aloud.'))
  assert.ok((await page.evaluate(() => window.__ed.drafts().length)) > 0, 'the words are a suggestion')
  await page.evaluate(() => { window.__take.on = false })

  await pickMode(page, 'Viewing')
  assert.deepEqual(await headHas(page), { mic: false, read: true, next: 'mode', anyMic: 0 }, 'Viewing')
  await shot(page, 'mic-viewing')

  await pickMode(page, 'Editing')
  assert.deepEqual(await headHas(page), { mic: true, read: true, next: 'mic', anyMic: 1 }, 'back to Editing')
  // A take running when the doc goes to Viewing is stopped, and nothing it
  // would still write lands.
  await page.click('.dc-head .mic')
  await page.evaluate(() => window.__dictate(' Before.'))
  await pickMode(page, 'Viewing')
  const t = await page.evaluate(() => ({ on: window.__take.on, stopped: window.__take.stopped, can: window.__ed.canType() }))
  assert.deepEqual(t, { on: false, stopped: 1, can: false })
  const before = await page.evaluate(() => window.__ed.text())
  assert.ok(before.includes('Before.'))
  await page.evaluate(() => { window.__take.on = true; window.__dictate(' After.') })
  assert.equal(await page.evaluate(() => window.__ed.text()), before, 'nothing is written in Viewing')
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('dictation: a doc that opens with no right to write has read aloud and no mic', { skip }, async () => {
  await start()
  const p = await newDoc('# Read only\n\nA line.\n')
  const page = await open({ doc: p, extra: '&mic&can=view' })
  await page.waitForSelector('.dc-head .read-aloud')
  const got = await headHas(page)
  assert.deepEqual([got.mic, got.read, got.anyMic], [false, true, 0])
  assert.equal(await page.evaluate(() => Boolean(document.querySelector('.dc-mpill-only'))), true, 'only Viewing')
  await page.done()
})

test('dictation, phone: the keyboard bar carries the mic in Editing and Suggesting, and no mic anywhere in Viewing', { skip }, async () => {
  await start()
  const p = await newDoc('# On a phone\n\nA first line of words.\n')
  const page = await open({ doc: p, w: 430, h: 900, phone: true, extra: '&mic' })
  const micIn = () => page.evaluate(() => ({
    bar: Boolean(document.querySelector('.dc-kbar.on .mic')),
    head: Boolean(document.querySelector('.dc-head .mic')),
    any: document.querySelectorAll('.dc .mic').length,
    read: Boolean(document.querySelector('.dc-head .read-aloud')),
  }))
  const focusIn = () => page.evaluate(() => { const v = window.__ed.view; v.focus(); v.dispatch({ selection: { anchor: v.state.doc.length - 2 } }) })
  for (const [i, [mode, want]] of [['Editing', true], ['Suggesting', true], ['Viewing', false], ['Editing', true]].entries()) {
    if (i) await pickMode(page, mode)
    await focusIn()
    await page.waitForTimeout(250)
    const m = await micIn()
    assert.deepEqual(m, { bar: want, head: false, any: want ? 1 : 0, read: true }, mode)
    await shot(page, `mic-phone-${mode.toLowerCase()}`)
  }
  assert.deepEqual(page.errors, [])
  await page.done()
})

test('dictation: a view link\'s visitor has no mic and cannot type', { skip }, async () => {
  await start()
  const p = await newDoc('# Shared to view\n\nA line.\n')
  const link = await devLink('view', 'Sam', p)
  const page = await visitor(link)
  const got = await page.evaluate(() => ({
    mic: document.querySelectorAll('.mic, [aria-label="Dictate"], [aria-label*="ictat"]').length,
    editable: document.querySelector('.cm-content').getAttribute('contenteditable'),
  }))
  assert.deepEqual(got, { mic: 0, editable: 'false' })
  await page.done()
})

test('a narrow window with a mouse (the phone\'s bars): Comment is offered after the button is up, not while it is held', { skip }, async () => {
  await start()
  const p = await newDoc(REPEATS)
  const page = await open({ doc: p, w: 430, h: 900, phone: true })
  const t = await page.evaluate(() => window.__ed.text())
  const s = t.indexOf('The river kept')
  const a = await pointAt(page, s), b = await pointAt(page, s + 'The river kept its own'.length, -1)
  const offered = () => page.evaluate(() => [...document.querySelectorAll('.dc-kbar.on .dc-kc')].some(x => /Comment/.test(x.textContent) && !x.disabled))
  await page.mouse.move(a.x, a.y)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) {
    await page.mouse.move(a.x + (b.x - a.x) * i / 10, a.y + (b.y - a.y) * i / 10)
    await page.waitForTimeout(i === 5 ? 700 : 40)
    assert.equal(await offered(), false, `not offered while the button is held (${i})`)
  }
  await page.mouse.up()
  await page.waitForTimeout(500)
  assert.equal(await offered(), true, 'offered after the release')
  assert.deepEqual(page.errors, [])
  await page.done()
})

// -- the measure on wide screens ------------------------------------------------------------------
//
// The text grows with the pane to 720 px and stops; past that the room is air.
// With nothing in the margin the text sits in the middle of the pane; with
// notes, the text and the margin are one block in the middle.
const MEASURE = 720
const geometry = (page) => page.evaluate(() => {
  const z = (() => { const r = document.querySelector('.dc'); return r.getBoundingClientRect().width / r.offsetWidth || 1 })()
  const t = document.querySelector('.cm-content').getBoundingClientRect()
  const p = document.querySelector('.dc-page').getBoundingClientRect()
  const m = document.querySelector('.dc-margin').getBoundingClientRect()
  const props = document.querySelector('.dc-props')?.getBoundingClientRect()
  return { text: t.width / z, left: (t.left - p.left) / z, right: (p.right - t.right) / z, margin: m.width / z,
    blockRight: (p.right - Math.max(t.right, m.right)) / z, propsLeft: props ? (props.left - t.left) / z : 0,
    bare: document.querySelector('.dc').classList.contains('bare') }
})

for (const zoom of [1, 0.9]) {
  test(`the text column never passes ${MEASURE} px, and centres with or without notes (${zoom * 100}%)`, { skip }, async () => {
    await start()
    const fresh = await newDoc('---\ncreated_by: Test\n---\n# Fresh\n\n' + 'A paragraph long enough to wrap across the whole measure several times over, so the line is as long as the column lets it be. '.repeat(6) + '\n')
    for (const w of [1280, 1470, 1920, 2560, 3400]) {
      for (const [doc, notes] of [['Essays/drafts/Long.md', true], [fresh, false]]) {
        const page = await open({ doc, w, zoom })
        const g = await geometry(page)
        const label = `${w}${notes ? ' with notes' : ' without'} at ${zoom * 100}%`
        assert.ok(g.text <= MEASURE + 0.5, `${label}: the text is ${g.text.toFixed(1)} px`)
        assert.ok(Math.abs(g.propsLeft) <= 1, `${label}: the properties line starts at the text's left edge (${g.propsLeft})`)
        assert.equal(g.bare, !notes, `${label}: the margin is shown only for notes`)
        if (!notes) {
          assert.ok(Math.abs(g.left - g.right) <= 2, `${label}: the text is centred (${g.left.toFixed(1)} | ${g.right.toFixed(1)})`)
        } else {
          assert.ok(g.margin >= 255, `${label}: the margin keeps its room (${g.margin})`)
          // the block (gutter, text, margin) sits in the middle: the air either side within the gutter's width
          assert.ok(Math.abs((g.left - 76) - g.blockRight) <= 4, `${label}: text and margin are one centred block (${(g.left - 76).toFixed(1)} | ${g.blockRight.toFixed(1)})`)
        }
        if (w * zoom >= 1900) assert.ok(g.text >= MEASURE - 0.5, `${label}: a wide pane gives the text its whole measure (${g.text.toFixed(1)})`)
        await shot(page, `measure-${w}-${notes ? 'notes' : 'bare'}-${zoom}`)
        assert.deepEqual(page.errors, [])
        await page.done()
      }
    }
  })
}

test(`the phone layout (the side panel's) holds the same ${MEASURE} px when wide, and is unchanged at 430`, { skip }, async () => {
  await start()
  for (const [w, want] of [[1100, MEASURE], [430, null]]) {
    const page = await open({ w, phone: true })
    const g = await geometry(page)
    if (want) {
      assert.ok(Math.abs(g.text - want) <= 0.5, `${w}: the text is ${g.text.toFixed(1)} px`)
      assert.ok(Math.abs((g.left - 38) - (g.right - 24)) <= 2, `${w}: centred (${g.left.toFixed(1)} | ${g.right.toFixed(1)})`)
    } else assert.ok(Math.abs(g.left - 38) <= 0.5 && Math.abs(g.right - 24) <= 0.5, `430: the phone's edges as before (${g.left} | ${g.right})`)
    const dots = await offsets(page, true)
    if (dots.length) near(dots, `${w}: dots level`)
    await page.done()
  }
})

for (const w of [1280, 1920]) {
  test(`a note's card stays level with its passage at ${w}, on load, scrolled and in Suggesting`, { skip }, async () => {
    await start()
    const page = await open({ w, h: 900 })
    const g = await geometry(page)
    assert.ok(g.text <= MEASURE + 0.5)
    let seen = 0
    for (const y of [0, 1200, 2600, 4000, 99999]) {
      await scrollTo(page, y)
      const o = await offsets(page, false)
      seen += o.length
      if (o.length) near(o, `${w}, scrolled to ${y}`)
    }
    assert.ok(seen >= 4, `notes were drawn and measured (${seen})`)
    await scrollTo(page, 0)
    await mode(page, 'Suggesting')
    for (const y of [0, 2600]) {
      await scrollTo(page, y)
      const o = await offsets(page, false)
      if (o.length) near(o, `${w} in Suggesting, scrolled to ${y}`)
    }
    assert.deepEqual(page.errors, [])
    await page.done()
  })
}

test('a doc without notes: a note arriving while keys go down moves the text aside only at the pause', { skip }, async () => {
  await start()
  const p = await newDoc(TEXT)
  const page = await open({ doc: p })
  const before = await geometry(page)
  assert.ok(before.bare, 'no notes: no margin')
  await caretAfter(page, 'learned my order.')
  const lefts = []
  let sent = false, arrivedWhileTyping = false
  for (const ch of ' And then more words, typed at an even pace while a note comes in from an agent.') {
    await page.keyboard.type(ch)
    await page.waitForTimeout(110)
    if (!sent && lefts.length === 5) { sent = true; await api(p, 'doc/notes', 'POST', { kind: 'comment', quote: 'the grey dog', text: 'From an agent, mid-sentence.' }, 'ada') }
    lefts.push((await geometry(page)).left)
    if (await page.evaluate(() => window.__ed.state.notes.some(n => n.state === 'open'))) arrivedWhileTyping = true
  }
  assert.ok(arrivedWhileTyping, 'the note reached the page while keys were going down')
  assert.ok(lefts.every(l => Math.abs(l - before.left) < 0.5), `the text stayed put while keys went down: ${lefts.map(l => l.toFixed(0)).join(',')}`)
  await page.waitForTimeout(1400)
  const after = await geometry(page)
  assert.ok(!after.bare && after.left < before.left - 20, `at the pause the text made room for the card (${before.left.toFixed(0)} → ${after.left.toFixed(0)})`)
  near(await offsets(page, false), 'the new card')
  // Starting a note moves it at once: resolve the agent's, then start one.
  for (const n of (await api(p, 'doc', 'GET')).notes) await api(p, `doc/notes/${n.id}/resolve`, 'POST', {}, 'ada')
  await until(page, () => document.querySelector('.dc').classList.contains('bare'), null, 6000)
  assert.deepEqual(page.errors, [])
  await page.done()
})
