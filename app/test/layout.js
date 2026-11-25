// Layout, for the few questions that are about where things land on a screen.
//
// `test/fakedom.js` has no layout engine, so it can say which nodes the page
// drew and never how wide one of them is. Two things here answer the second
// question, at two strengths:
//
// - `rendered()` puts the page's own tree — the fixture's nodes, serialised —
//   under the page's own stylesheet in a real Chromium, at a real viewport, and
//   runs a measuring function there. **It runs only where a Chromium is named
//   by `LOAM_CHROME`** (the path to a `chrome` or `chrome-headless-shell`;
//   whatever libraries it needs are the caller's environment's business), and
//   answers `null` everywhere else, so a test says plainly that it skipped.
// - `resolved()` reads `public/style.css` itself: the declarations that apply
//   to a node of the page's tree at a viewport width, in cascade order. It is
//   a small cascade — class selectors and the descendant combinator, the width
//   and pointer media features this stylesheet uses — and it runs everywhere,
//   so the rules a layout rests on are held even where nothing can paint them.

import { readFileSync, writeFileSync, mkdtempSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { execFileSync, spawn } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

const PUBLIC = fileURLToPath(new URL('../public/', import.meta.url))
const CSS = readFileSync(join(PUBLIC, 'style.css'), 'utf8')

const escapeText = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const escapeAttr = (s) => escapeText(s).replace(/"/g, '&quot;')

// A fixture node as HTML. The style attribute is the declarations the page
// wrote through `style`, which is what a browser would have on the node.
export function serialize(node) {
  if (node.nodeType === 3) return escapeText(String(node.textContent ?? ''))
  if (node.nodeType !== 1) return ''
  const tag = node.tag
  const attrs = node.attributes.map(({ name, value }) => ` ${name}="${escapeAttr(value)}"`).join('')
  return `<${tag}${attrs}>${node.childNodes.map(serialize).join('')}</${tag}>`
}

export const chrome = () => process.env.LOAM_CHROME || null

// The page's tree at `width` × `height`, with `measure` run against it; its
// answer comes back through JSON. `coarse` is a phone's pointer, which is what
// the 44px targets are keyed off. `null` when there is no Chromium to ask.
export function rendered(root, { width, height = 900, coarse = false, theme = 'light' }, measure) {
  const bin = chrome()
  if (!bin) return null
  const dir = mkdtempSync(join(tmpdir(), 'loam-layout-'))
  try {
    const page = join(dir, 'page.html')
    writeFileSync(
      page,
      `<!doctype html><html lang="en" data-theme="${theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="file://${PUBLIC}fonts/fonts.css">
<link rel="stylesheet" href="file://${PUBLIC}style.css">
</head><body>${serialize(root).replace(/^<div/, '<div id="app"')}
<pre id="__out"></pre>
<script>
document.fonts.ready.then(() => {
  const answer = (${measure.toString()})(document)
  document.getElementById('__out').textContent = JSON.stringify({ coarse: matchMedia('(pointer: coarse)').matches, innerWidth, answer })
})
</script></body></html>`,
    )
    const args = [
      '--headless',
      '--no-sandbox',
      '--disable-gpu',
      '--hide-scrollbars',
      `--window-size=${width},${height}`,
      '--virtual-time-budget=3000',
      '--allow-file-access-from-files',
      ...(coarse ? ['--blink-settings=primaryPointerType=2,availablePointerTypes=2,primaryHoverType=1,availableHoverTypes=1'] : []),
      '--dump-dom',
      `file://${page}`,
    ]
    const dom = execFileSync(bin, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 30000 })
    const out = /<pre id="__out">([\s\S]*?)<\/pre>/.exec(dom)?.[1]
    if (!out) throw new Error('test/layout.js: the page answered nothing')
    const parsed = JSON.parse(out.replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'))
    if (parsed.innerWidth !== width) throw new Error(`test/layout.js: asked for a ${width}px viewport and got ${parsed.innerWidth}px`)
    if (parsed.coarse !== coarse) throw new Error(`test/layout.js: asked for pointer ${coarse ? 'coarse' : 'fine'} and did not get it`)
    return parsed.answer
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

// `rendered()` reads the page with `--dump-dom`, and a page read that way is
// never given a frame: no `requestAnimationFrame`, no `ResizeObserver`. A
// question about what happens *as* a page changes — the transcript following
// its end — needs both, so `driven()` runs the browser over its DevTools
// protocol instead: `body` is the test's own HTML under the page's stylesheet,
// `script` is source run before it, and `drive` is an async function of the
// document whose answer comes back through JSON. `null` without a Chromium.
export async function driven({ width, height = 900, theme = 'light', body, script = '' }, drive) {
  const bin = chrome()
  if (!bin) return null
  const dir = mkdtempSync(join(tmpdir(), 'loam-driven-'))
  const page = join(dir, 'page.html')
  writeFileSync(
    page,
    `<!doctype html><html lang="en" data-theme="${theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="file://${PUBLIC}fonts/fonts.css">
<link rel="stylesheet" href="file://${PUBLIC}style.css">
</head><body>${body}<script>${script}</script></body></html>`,
  )
  const proc = spawn(
    bin,
    ['--headless', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--remote-debugging-port=0', `--user-data-dir=${join(dir, 'profile')}`, `--window-size=${width},${height}`, '--allow-file-access-from-files', `file://${page}`],
    { stdio: 'ignore', detached: true },
  )
  const exited = new Promise((resolve) => proc.once('exit', resolve))
  let ws = null
  try {
    const port = await new Promise((resolve, reject) => {
      const began = Date.now()
      const look = () => {
        const file = join(dir, 'profile', 'DevToolsActivePort')
        const first = existsSync(file) ? readFileSync(file, 'utf8').split('\n')[0] : ''
        if (first) return resolve(first)
        if (Date.now() - began > 30000) return reject(new Error('test/layout.js: the browser did not start in 30 s'))
        setTimeout(look, 50)
      }
      look()
    })
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
    ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
    await new Promise((resolve, reject) => {
      ws.onopen = resolve
      ws.onerror = reject
    })
    let id = 0
    const waiting = new Map()
    ws.onmessage = (m) => {
      const d = JSON.parse(m.data)
      if (d.id && waiting.has(d.id)) waiting.get(d.id)(d)
    }
    const send = (method, params = {}) =>
      new Promise((resolve) => {
        id += 1
        waiting.set(id, resolve)
        ws.send(JSON.stringify({ id, method, params }))
      })
    // CDP can attach to the initial about:blank target before its file
    // navigation commits. Evaluating the drive there loses its context when
    // that navigation arrives; wait for this fixture's completed document.
    const readyUntil = Date.now() + 10000
    for (;;) {
      const ready = await send('Runtime.evaluate', {
        expression: `location.href === ${JSON.stringify(pathToFileURL(page).href)} && document.readyState === 'complete'`,
        returnByValue: true,
      })
      if (ready.result?.result?.value === true) break
      if (Date.now() >= readyUntil) throw new Error('test/layout.js: the fixture document did not finish navigating')
      await new Promise(resolve => setTimeout(resolve, 25))
    }
    const expression = `Promise.resolve()
      .then(() => document.fonts.ready)
      .then(() => (${drive.toString()})(document))
      .then((answer) => JSON.stringify({ innerWidth, answer }))`
    const out = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (out.error) throw new Error(`test/layout.js: Chromium evaluation failed: ${out.error.message}`)
    if (out.result?.exceptionDetails) throw new Error(`test/layout.js: ${out.result.exceptionDetails.exception?.description ?? out.result.exceptionDetails.text}`)
    const parsed = JSON.parse(out.result.result.value)
    if (parsed.innerWidth !== width) throw new Error(`test/layout.js: asked for a ${width}px viewport and got ${parsed.innerWidth}px`)
    return parsed.answer
  } finally {
    try {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ id: 999999, method: 'Browser.close', params: {} }))
      }
    } catch {}
    ws?.close()
    // `/usr/bin/chromium-headless-shell` on Debian is a `/bin/sh` wrapper without
    // `exec`; killing only `proc.pid` orphans the real browser and its renderer
    // tree. Signal the whole process group and fall back to SIGKILL.
    const killGroup = (sig) => {
      try { process.kill(-proc.pid, sig) } catch { try { proc.kill(sig) } catch {} }
    }
    killGroup('SIGTERM')
    await Promise.race([exited, new Promise((r) => setTimeout(r, 1500))])
    killGroup('SIGKILL')
    rmSync(dir, { recursive: true, force: true })
  }
}

// --- the stylesheet, read ---------------------------------------------------

// Every style rule, with the media queries it sits inside.
function rules(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = []
  const walk = (text, media) => {
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
      if (head.startsWith('@media')) walk(body, [...media, head.slice(6).trim()])
      else if (!head.startsWith('@')) out.push({ selectors: head.split(',').map((s) => s.trim()), body, media })
      i = j
    }
  }
  walk(clean, [])
  return out
}

const RULES = rules(CSS)

// Whether one `@media` prelude holds at this viewport. Only the features this
// stylesheet uses; anything else is an error rather than a guess.
function holds(query, { width, coarse }) {
  return query.split(',').some((part) => {
    let q = part.trim()
    const negated = /^not all and\s+/.test(q)
    if (negated) q = q.replace(/^not all and\s+/, '')
    const each = [...q.matchAll(/\(([^)]+)\)/g)].map((m) => m[1].trim())
    const ok = each.every((f) => {
      let m
      if ((m = /^min-width:\s*([\d.]+)px$/.exec(f))) return width >= Number(m[1])
      if ((m = /^max-width:\s*([\d.]+)px$/.exec(f))) return width <= Number(m[1])
      if (f === 'pointer: coarse') return coarse
      if (f === 'pointer: fine') return !coarse
      if (f === 'hover: hover') return !coarse
      if (f === 'hover: none') return coarse
      if (f === 'prefers-reduced-motion: reduce') return false
      if (f === 'forced-colors: active') return false
      throw new Error(`test/layout.js: no reading of the media feature (${f})`)
    })
    return negated ? !ok : ok
  })
}

const classes = (node) => (node.getAttribute?.('class') ?? '').split(/\s+/).filter(Boolean)

// One compound selector — classes only, and a tag — against one node.
function compound(sel, node) {
  if (node?.nodeType !== 1) return false
  const m = /^([a-z][a-z0-9]*)?((?:\.[A-Za-z0-9_-]+)*)$/.exec(sel)
  if (!m) return false
  if (m[1] && m[1] !== node.tag) return false
  const have = classes(node)
  return m[2]
    .split('.')
    .filter(Boolean)
    .every((c) => have.includes(c))
}

// A selector of compounds joined by descendant combinators. Anything with a
// pseudo-class, an attribute or another combinator is not this reader's and
// never matches, which is right for the layout rules it is asked about.
function matches(selector, node) {
  const parts = selector.split(/\s+/)
  if (!compound(parts.at(-1), node)) return false
  let at = node.parentNode
  for (let k = parts.length - 2; k >= 0; k -= 1) {
    while (at && !compound(parts[k], at)) at = at.parentNode
    if (!at) return false
    at = at.parentNode
  }
  return true
}

const specificity = (sel) => [(sel.match(/\./g) ?? []).length, (sel.match(/(^|\s)[a-z]/g) ?? []).length]

// The declarations that apply to `node` at this viewport, later and more
// specific winning, as a plain object of property → value. Shorthands are
// left as written; ask for the one a rule writes.
export function resolved(node, { width, coarse = false }) {
  const hits = []
  RULES.forEach((rule, order) => {
    if (!rule.media.every((q) => holds(q, { width, coarse }))) return
    const best = rule.selectors.filter((s) => matches(s, node)).map(specificity).sort((a, b) => b[0] - a[0] || b[1] - a[1])[0]
    if (best) hits.push({ rule, spec: best, order })
  })
  hits.sort((a, b) => a.spec[0] - b.spec[0] || a.spec[1] - b.spec[1] || a.order - b.order)
  const out = {}
  for (const { rule } of hits) {
    for (const decl of rule.body.split(';')) {
      const at = decl.indexOf(':')
      if (at < 0) continue
      out[decl.slice(0, at).trim()] = decl.slice(at + 1).trim()
    }
  }
  return out
}
