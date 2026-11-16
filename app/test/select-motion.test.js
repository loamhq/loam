// How selection moves, read off the stylesheet.
//
// The owner, 2026-10-05: "a very smooth and beautiful and alive-looking, very
// subtle minimal animation." The brief's bounds, held here: every movement in
// selection is short — 250 ms at most, the pill's change from the tab bar to
// the action bar alone allowed to 300 — nothing overshoots its resting state,
// and a person who asks for less movement gets none of it: no animation, no
// transition, no transform.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const CSS = readFileSync(new URL('../public/style.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

// Every rule, flat, with its media and whether it is a keyframes block.
function rules(text, media = []) {
  const out = []
  let i = 0
  while (i < text.length) {
    const open = text.indexOf('{', i)
    if (open < 0) break
    const head = text.slice(i, open).trim()
    let depth = 1
    let j = open + 1
    while (j < text.length && depth > 0) {
      if (text[j] === '{') depth += 1
      else if (text[j] === '}') depth -= 1
      j += 1
    }
    const body = text.slice(open + 1, j - 1)
    if (head.startsWith('@media')) out.push(...rules(body, [...media, head]))
    else if (head.startsWith('@keyframes')) out.push({ keyframes: head.slice(10).trim(), body, media })
    else out.push({ selectors: head.split(',').map((s) => s.trim()), body, media })
    i = j
  }
  return out
}

const ALL = rules(CSS)
const reduced = (r) => r.media.some((m) => /prefers-reduced-motion/.test(m))

// The selection's pieces: the rows' circles and fill, both headers, the bar
// and the tab bar coming back, the notice, the rename field, the sheet.
const SELECTION = /srow|selecting|\.swap|selbar|selbtn|selmore|rowfield|listnotice|\.rising/
const moving = ALL.filter((r) => r.selectors && !reduced(r) && r.selectors.some((s) => SELECTION.test(s)) && /(^|;)\s*(animation|transition)(-[a-z]+)?\s*:/.test(r.body))
const OURS = ['loamCheck', 'loamPillIn', 'loamGrow', 'loamNoticeLift', 'loamNoticeDrop', 'loamRise', 'loamLift']

test('selection moves at all: the circles, the fill, the headers, the bar, the notice, the field and the sheet', () => {
  const sel = moving.flatMap((r) => r.selectors)
  for (const want of ['.srow-check', '.srow-check.on', '.srow', '.home-head.selecting > *', '.list-head.selecting > *', '.selbar-pill', '.nav.swap .navpill', '.listnotice:not(.inline)', '.listnotice.leaving', '.srow .rowfield', '.actions-sheet.rising', '.scrim.rising']) {
    assert.ok(sel.includes(want), `${want} does not move`)
  }
})

test('every movement is within the bounds: 250 ms, and the pill’s change 300', () => {
  for (const r of moving) {
    const decl = r.body.split(';').filter((d) => /^\s*(animation|transition)/.test(d)).join(';')
    const pill = /loamPillIn/.test(decl)
    // Durations and delays alike — a delay is time a person waits too.
    const times = [...decl.matchAll(/(\d*\.?\d+)(ms|s)\b/g)].map((m) => Number(m[1]) * (m[2] === 's' ? 1000 : 1))
    for (const ms of times) assert.ok(ms <= (pill ? 300 : 250), `${r.selectors.join(', ')}: ${ms} ms in “${decl.trim()}”`)
  }
})

test('nothing bounces: no keyframe past its resting size, and no curve that overshoots', () => {
  for (const name of OURS) {
    const k = ALL.find((r) => r.keyframes === name)
    assert.ok(k, `no @keyframes ${name}`)
    for (const m of k.body.matchAll(/scale\(([\d.]+)\)/g)) assert.ok(Number(m[1]) <= 1, `${name} scales to ${m[1]}`)
  }
  for (const r of moving) {
    for (const m of r.body.matchAll(/cubic-bezier\(([^)]*)\)/g)) {
      const [, y1, , y2] = m[1].split(',').map(Number)
      assert.ok(y1 >= 0 && y1 <= 1 && y2 >= 0 && y2 <= 1, `${r.selectors.join(', ')}: cubic-bezier(${m[1]}) overshoots`)
    }
  }
})

test('less movement stills every one of them: no animation, no transition, no transform', () => {
  const still = ALL.filter((r) => r.selectors && reduced(r))
  const has = (sel, prop, value) => still.some((r) => (r.selectors.includes(sel) || r.selectors.includes(sel.replace(':not(.inline)', ''))) && new RegExp(`${prop}\\s*:\\s*${value}\\s*!important`).test(r.body))
  for (const sel of new Set(moving.flatMap((r) => r.selectors))) {
    // A hover rule's only motion is its delay; the rule it unfolds is stilled.
    if (/:hover/.test(sel)) continue
    assert.ok(has(sel, 'animation', 'none'), `${sel} still animates under reduced motion`)
    assert.ok(has(sel, 'transition', 'none'), `${sel} still transitions under reduced motion`)
  }
  assert.ok(has('.srow-check', 'transform', 'none'), 'the folded circle keeps its transform')
})
