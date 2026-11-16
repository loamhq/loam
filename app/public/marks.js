// The providers' own marks, and the ring that goes round one.
//
// A provider is a vendor account, and a vendor has a mark. The page drew a
// letter in a tile for each — "C" for Claude Code — which is what a page does
// when it has no mark to draw; the owner, 2026-09-22: "instead of saying running
// on Claude Code and having 'c', can we have the anthropic logo instead?"
//
// **Every mark here is inline SVG, monochrome, and takes its colour from the
// text colour of whatever it sits in** — `fill: currentColor`, no raster, no
// second copy for the dark theme. A tile that tints its own ink (the rows'
// 18px badge, Settings' 40px tile) therefore tints the mark with it.
//
// Where each came from is worth writing down, because not all of them are the
// vendor's own geometry (GitHub's, for the Integrations card, is simple-icons'
// copy of the published mark, like the three below):
//
// - **Anthropic** (`claude-code`), **OpenAI** (`codex`) and **OpenCode** — the
//   published marks, taken from the simple-icons distribution of each vendor's
//   brand asset, path data unchanged, on the 24-unit box they are drawn on.
// - **xAI** (`grok`) — **a faithful simplified redraw, not the vendor's own
//   path.** xAI publishes no mark this box could fetch (its brand page and the
//   icon mirrors are behind a challenge), so this is the mark's geometry as it
//   reads: an X whose falling diagonal is broken where the rising one crosses
//   it. If the real path is ever to hand, it replaces the `grok` entry here and
//   nothing else in the app changes.
// - **Antigravity** — an app-drawn rising A glyph, not an official vendor logo.
//   It uses the same monochrome 24-unit box as the vendor marks.
//
// A provider this table has never heard of is not a gap to fill with a guess:
// it falls back to the letter the page drew before, which is `providerGlyph()`
// in sessions.js and is passed in rather than imported, so this file draws and
// decides nothing else.

import { dress } from './ui.js'

const NS = 'http://www.w3.org/2000/svg'

// Each entry is the `d` of one path on a 24-unit box, filled rather than
// stroked. Nothing here is a colour.
const MARKS = {
  // Anthropic.
  'claude-code':
    'M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z',
  // OpenAI.
  codex:
    'M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z',
  // Antigravity: rising A, drawn for this app.
  antigravity: 'M12 2 1.5 21h4.6L12 9.8 17.9 21h4.6L12 2zM12 14l-3.1 5.6H15.1L12 14z',
  // xAI, redrawn: the rising diagonal whole, the falling one in two pieces.
  grok: 'M16.2 2h5.3L7.8 22H2.5zM2.5 2h5.3l5.48 8h-5.3zm8.2 11.6h5.3L21.5 22h-5.3z',
  // OpenCode.
  opencode: 'M22 24H2V0h20zM17 4.8H7v14.4h10z',
  // GitHub — not a provider but the first integration, drawn the same way.
  github:
    'M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12',
}

export function hasMark(id) {
  return typeof id === 'string' && id in MARKS
}

// The provider's mark at `size`, or — for a provider this table does not know —
// whatever the caller would have drawn instead, which is the letter.
export function providerMark(id, size = 12, fallback = null) {
  if (!hasMark(id)) {
    const span = document.createElement('span')
    span.className = 'mark-letter'
    span.textContent = fallback ?? ''
    return span
  }
  const root = document.createElementNS(NS, 'svg')
  root.setAttribute('width', size)
  root.setAttribute('height', size)
  root.setAttribute('viewBox', '0 0 24 24')
  root.setAttribute('fill', 'currentColor')
  root.setAttribute('aria-hidden', 'true')
  root.setAttribute('class', 'mark')
  // What the tests read, and what a person reading the DOM reads: which mark
  // this is, without having to recognise a path.
  root.setAttribute('data-mark', id)
  dress(root, { flex: 'none' })
  const path = document.createElementNS(NS, 'path')
  path.setAttribute('d', MARKS[id])
  root.append(path)
  return root
}

// ---------------------------------------------------------------- the ring

// The circumference of the arc, in the units the `viewBox` is drawn in. The
// radius is chosen so that it is **100**, which is the whole trick: the filled
// length of the arc is then the percentage itself, so `stroke-dasharray` reads
// `69 100` at 69% and there is no arithmetic between what the quota says and
// what the ring draws.
const R = 15.9155
const BOX = 36

// The provider's mark inside a circle, with the subscription's usage drawn
// round it. `level` is the colour band — the stylesheet owns which colour each
// band is — and `percent` is `null` when the host has seen no reading yet,
// which draws **the mark alone**: no track, no arc, the same box. An empty
// track is not 0%: one says nothing is known, the other says nothing is used,
// and the clearest way to say nothing is known is to draw no gauge at all.
//
// The band is `qring-<level>` and never the bare word. It was `qring empty`
// until 2026-10-01, and `.empty` is the page's empty-state block — 36px by 30px
// of padding — so a chat with no reading drew its mark inside a ring swollen
// past the buttons beside it, the track stretched thick with it. Seen on the
// first Codex chats, whose providers row carried `usage: null`.
export function usageRing(id, { percent = null, level = 'empty', size = 32, fallback = null } = {}) {
  const wrap = document.createElement('span')
  wrap.className = `qring qring-${level}`
  // `dress()` and not two property writes: every style a reconciled node
  // carries is on its own record, which is the only thing a later redraw
  // compares against.
  dress(wrap, { width: `${size}px`, height: `${size}px` })

  if (percent !== null) {
    const svg = document.createElementNS(NS, 'svg')
    svg.setAttribute('viewBox', `0 0 ${BOX} ${BOX}`)
    svg.setAttribute('class', 'qring-arc')
    svg.setAttribute('aria-hidden', 'true')
    svg.append(circle('track'))
    const arc = circle('arc')
    arc.setAttribute('stroke-dasharray', `${round(percent)} 100`)
    // From the top, clockwise, which is the only way a ring reads as a gauge.
    arc.setAttribute('transform', `rotate(-90 ${BOX / 2} ${BOX / 2})`)
    arc.setAttribute('stroke-linecap', 'round')
    svg.append(arc)
    wrap.append(svg)
  }
  wrap.append(providerMark(id, Math.round(size * 0.45), fallback))
  return wrap
}

function circle(cls) {
  const el = document.createElementNS(NS, 'circle')
  el.setAttribute('cx', BOX / 2)
  el.setAttribute('cy', BOX / 2)
  el.setAttribute('r', R)
  el.setAttribute('fill', 'none')
  el.setAttribute('class', cls)
  return el
}

function round(percent) {
  const n = Math.min(100, Math.max(0, percent))
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}
