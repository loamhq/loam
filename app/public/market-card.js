// A listing card in an agent's reply (2026-11-18). Pure functions, no DOM;
// the card itself is drawn by public/page/market.js (`marketCards`), placed
// by public/threads.js, and the tests are test/market-card.test.js.
//
// **The form an agent writes** is one line of its own holding nothing but a
// Markdown link to the listing's page in the app:
//
//   [Pocket Sync](https://<the app>/?modal=market&listing=pocket-sync)
//
// The Market tool (`mcp__engineer__market`, bin/loam-market search) hands
// back that exact line as each result's `card`, so an agent copies it rather
// than composing it. In the thread record (15_CONVERSATIONS/, plain
// Markdown) it is what it looks like: the listing's name, linked to its page.
// In the app the line becomes the card. It is hard to make by accident: the
// link must be alone on its line, outside code, and its address must be the
// app's root with exactly `modal=market` and a listing id, nothing else. A
// line that is almost that is drawn as the Markdown it is. An id the Market
// does not list is drawn as its words, plain (the page decides that, since
// only it knows the list).
//
// **A plugin by its link** (2026-11-18): the same line with `install=` and a
// GitHub link in place of `listing=`, for something the Market does not list
// (a friend's repository, a plugin shared by link):
//
//   [Foundry](https://<the app>/?modal=market&install=https%3A%2F%2Fgithub.com%2Fowner%2Frepo)
//
// is a card whose Install opens the install-from-link sheet with that link,
// where what it declares is read and shown before any yes. `bin/loam-market
// link <link>` hands back that line as `card`.
//
// At most MARKET_CARDS_MAX in one message: a card is an offer, and a wall of
// them is a catalogue. Lines past the limit stay in the text as links.

export const MARKET_CARDS_MAX = 3
const ID = /^[a-z][a-z0-9-]*$/
const LINE = /^\s{0,3}\[([^\[\]\n]{1,120})\]\(\s*<?(https?:\/\/[^\s()<>]+?)>?\s*\)\s*$/

// The address of a listing's page in the app, for an origin.
export const listingAddress = (origin, id) => `${String(origin).replace(/\/+$/, '')}/?modal=market&listing=${id}`
export const linkAddress = (origin, link) => `${String(origin).replace(/\/+$/, '')}/?modal=market&install=${encodeURIComponent(link)}`
const GITHUB_LINK = /^(?:https:\/\/github\.com\/[\w.-]+\/[\w.-]+(?:[/?#]\S*)?|[\w-][\w.-]*\/[\w.-]+)$/

// The listing a line names, `{ id, label }` (or a link's, `{ link, label }`),
// or null when it is not a card.
export function cardRef(line) {
  const m = LINE.exec(String(line ?? ''))
  if (!m) return null
  let url
  try { url = new URL(m[2]) } catch { return null }
  if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.hash) return null
  if (url.pathname !== '/' && url.pathname !== '') return null
  const keys = [...url.searchParams.keys()]
  if (keys.length !== 2 || url.searchParams.get('modal') !== 'market') return null
  if (keys.includes('install')) {
    const link = url.searchParams.get('install') ?? ''
    if (link.length > 400 || !GITHUB_LINK.test(link)) return null
    return { link, label: m[1].trim() }
  }
  const id = url.searchParams.get('listing')
  if (!keys.includes('listing') || !id || !ID.test(id) || id.length > 64) return null
  return { id, label: m[1].trim() }
}

// A message's text cut into what is words and what is cards, in order:
// `[{ text }, { cards: [{ id, label }] }, { text }, …]`. Cards on lines next
// to each other (blank lines between are fine) are one group. Fenced code is
// never read for cards. Text with no card is one piece, the text unchanged.
export function splitCards(text) {
  const src = String(text ?? '')
  if (!src.includes('modal=market')) return [{ text: src }]
  const lines = src.split('\n'), out = []
  let words = [], group = null, fence = null, count = 0, blanks = []
  const flushWords = () => { if (words.length) { out.push({ text: words.join('\n') }); words = [] } }
  for (const line of lines) {
    const bare = line.trim()
    if (fence) {
      if (bare.startsWith(fence)) fence = null
      words.push(line); continue
    }
    const open = /^(`{3,}|~{3,})/.exec(bare)
    if (open) { if (group) { group = null; words.push(...blanks) }; blanks = []; fence = open[1]; words.push(line); continue }
    const ref = count < MARKET_CARDS_MAX ? cardRef(line) : null
    if (ref) {
      if (!group) { flushWords(); group = { cards: [] }; out.push(group) }
      group.cards.push(ref); count++; blanks = []; continue
    }
    if (group && bare === '') { blanks.push(line); continue }
    if (group) { group = null; blanks = [] }
    words.push(line)
  }
  flushWords()
  if (!out.some(p => p.cards)) return [{ text: src }]
  const pieces = out.map(p => p.text !== undefined ? { text: p.text.replace(/^\n+|\n+$/g, '') } : p).filter(p => p.cards || p.text.trim())
  return pieces.length ? pieces : [{ text: src }]
}

// What a card says when it cannot be one: the words of its link, as written.
export const plainWords = ref => ref.label || ref.id
