// The Market's reading of the list (2026-11-18): search, the lead story, the
// sections, the words a listing is shown with, and the request an agent is
// asked to build when nothing is quite right. Pure functions, no DOM; the
// page is public/page/market.js and the tests are test/market.test.js.
//
// Search is ask-first: a person types what they want ("sync with my
// phone"), not a name. A query is cut into words, the small words dropped,
// each word cut to its stem ("writing" and "write" are both "writ"), and a
// listing scores for each word its name, kind, author or description holds,
// the name counting most. A word can also be met by a near word
// (SYNONYMS: "phone" by "mobile"), for less. Anything that scores is a
// result; the best first.

import { formatDate } from './time.js'

export const KINDS = ['theme', 'extension', 'app', 'agent', 'skill']
export const KIND_ONE = { theme: 'Theme', extension: 'Extension', app: 'App', agent: 'Agent', skill: 'Skill' }
export const KIND_MANY = { theme: 'Themes', extension: 'Extensions', app: 'Apps', agent: 'Agents', skill: 'Skills' }
export const KIND_ABOUT = {
  theme: 'How Loam looks: colour, type and light.',
  extension: 'New things Loam and your agents can do.',
  app: 'Programs that run on your server, opened from Apps.',
  agent: 'New characters to talk with, each starting fresh with you.',
  skill: 'Ways of working, for every agent.',
}
// A section's quiet subtitle on the front page, beside its name.
export const KIND_LINE = {
  theme: 'Rooms to work in',
  extension: 'New things your agents can do',
  app: 'Programs on your own server',
  agent: 'New characters to talk with',
  skill: 'Ways of working, for every agent',
}
// The Market's one line under its name, on the right of the masthead.
export const TAGLINE = 'Themes, extensions and apps for your Loam, made by people who use it.'
// Where a thing lives once it is installed: what the install sheet says.
export const KIND_HOME = { theme: 'Appearance', extension: 'Plugins', app: 'Apps', agent: 'Agents', skill: 'Plugins' }

const STOP = new Set(('a an and any are as at be but by can could do does for from get give have help how i ' +
  'in into is it its just let like make me more my need of on one or our please so some something ' +
  'that the their them then there they thing things this to too up us want way we what when where which while who will with ' +
  'would you your').split(' '))

// Near words, both ways. Small on purpose: the obvious neighbours only.
const NEAR = [
  ['phone', 'mobile', 'device', 'laptop', 'tablet'],
  ['picture', 'image', 'photo', 'drawing', 'illustration', 'art'],
  ['sync', 'synchronise', 'synchronize', 'mirror', 'backup'],
  ['write', 'writer', 'draft', 'essay', 'prose'],
  ['colour', 'color', 'palette', 'theme', 'look'],
  ['dark', 'night', 'evening'],
  ['calm', 'quiet', 'soft', 'gentle'],
  ['note', 'notes', 'journal', 'diary'],
  ['voice', 'speech', 'audio', 'speak', 'listen'],
  ['task', 'todo', 'habit', 'routine'],
  ['calendar', 'schedule', 'date'],
  ['code', 'program', 'develop', 'build'],
]

// A word's stem: plural, -ing and -ed taken off, a final e and a doubled
// last letter too, so "write", "writes" and "writing" meet at "writ", and
// "run" and "running" at "run". Short words are left as they are.
export function stem(word) {
  let w = String(word).toLowerCase().replace(/['’]s$/, '')
  if (w.length <= 3) return w
  if (w.endsWith('ies') && w.length > 4) w = w.slice(0, -3) + 'y'
  else if (w.endsWith('ing') && w.length > 5) w = w.slice(0, -3)
  else if (w.endsWith('ed') && w.length > 4) w = w.slice(0, -2)
  else if (/(ss|x|z|ch|sh)es$/.test(w)) w = w.slice(0, -2)
  else if (w.endsWith('s') && !w.endsWith('ss') && w.length > 3) w = w.slice(0, -1)
  if (w.endsWith('e') && w.length > 3) w = w.slice(0, -1)
  if (/([b-df-hj-np-tv-z])\1$/.test(w) && !/(ll|ss|zz)$/.test(w)) w = w.slice(0, -1)
  return w
}

const split = text => String(text ?? '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').split(/[^a-z0-9]+/).filter(Boolean)

// The words of a query that carry meaning, as stems, each with its near
// words' stems.
export function queryWords(q) {
  const seen = new Set(), out = []
  for (const word of split(q)) {
    if (STOP.has(word) || word.length < 2) continue
    const s = stem(word)
    if (seen.has(s)) continue
    seen.add(s)
    const near = new Set()
    for (const group of NEAR) if (group.some(g => stem(g) === s)) for (const g of group) if (stem(g) !== s) near.add(stem(g))
    out.push({ word, stem: s, near: [...near] })
  }
  return out
}

// Whether a word of the listing meets a stem the person typed: the same
// stem, or one beginning the other when the shorter has four letters or
// more, so "photo" finds "photographs" and "calmer" finds "calm".
const meets = (stemmed, s) => stemmed === s || (s.length >= 4 && stemmed.startsWith(s)) || (stemmed.length >= 4 && s.startsWith(stemmed))

function fieldScore(words, s) { return words.some(w => meets(w, s)) }

// How well one listing answers a query, and which of its words did.
export function score(entry, q) {
  const words = queryWords(q)
  if (!words.length) return 0
  // A listing's small words ("them", "with") meet nothing: "themes" is not "them".
  const read = text => split(text).filter(w => !STOP.has(w)).map(stem)
  const fields = {
    name: read(entry.name),
    kind: (entry.kind ?? []).flatMap(k => [k, KIND_MANY[k] ?? '']).flatMap(read),
    author: read(entry.author),
    description: read(entry.description),
    id: read(entry.id),
  }
  const weight = { name: 6, id: 4, kind: 3, author: 2, description: 2 }
  let total = 0, met = 0
  for (const { stem: s, near } of words) {
    let best = 0
    for (const [field, list] of Object.entries(fields)) {
      if (fieldScore(list, s)) best = Math.max(best, weight[field])
      else if (near.some(n => fieldScore(list, n))) best = Math.max(best, weight[field] / 2)
    }
    if (best) met++
    total += best
  }
  // Meeting every word the person typed counts for more than meeting one
  // of them well.
  return met ? total + (met === words.length ? 4 : 0) + met : 0
}

// The listings that answer a query (and are of a kind, unless 'all'), the
// best first; names break ties. An empty query is every listing of the kind.
export function search(entries, q = '', kind = 'all') {
  const pool = (entries ?? []).filter(e => kind === 'all' || (e.kind ?? []).includes(kind))
  if (!queryWords(q).length) return [...pool]
  return pool.map(e => [e, score(e, q)]).filter(([, s]) => s > 0)
    .sort((a, b) => b[1] - a[1] || a[0].name.localeCompare(b[0].name)).map(([e]) => e)
}

// A text cut into what to draw plain and what to draw marked: every word
// that meets a word the person typed (not its near words: a mark says "you
// typed this").
export function highlight(text, q) {
  const text_ = String(text ?? '')
  const stems = queryWords(q).map(w => w.stem)
  if (!stems.length || !text_) return [{ text: text_, hit: false }]
  const out = []
  let last = 0
  for (const m of text_.matchAll(/[\p{L}\p{N}]+/gu)) {
    const word = m[0].toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')
    if (STOP.has(word) || !stems.some(s => meets(stem(word), s))) continue
    if (m.index > last) out.push({ text: text_.slice(last, m.index), hit: false })
    out.push({ text: m[0], hit: true })
    last = m.index + m[0].length
  }
  if (last < text_.length) out.push({ text: text_.slice(last), hit: false })
  return out.length ? out : [{ text: text_, hit: false }]
}

export const kindOf = entry => KINDS.find(k => (entry?.kind ?? []).includes(k)) ?? 'extension'
// Made by Loam: published from Loam's own GitHub organisation, or signed
// "Loam …" (2026-11-18: the first-party listings now name their creator, so
// the author alone no longer says it).
export const LOAM_ORG = 'loamhq'
export const byLoam = entry => String(entry?.repo ?? '').split('/')[0].toLowerCase() === LOAM_ORG || /^loam\b/i.test(String(entry?.author ?? '').trim())

// A listing's colour when it has nothing else to show: one of the persona
// hues, the same for the same id everywhere (its letter tile, a theme's face).
const HUES = ['--persona-teal', '--persona-rust', '--persona-olive', '--persona-plum', '--persona-gold', '--persona-blue', '--persona-sage', '--persona-rose', '--persona-taupe', '--persona-slate']
export function hueOf(id) {
  let n = 0
  for (const ch of String(id ?? '')) n = (n * 31 + ch.codePointAt(0)) >>> 0
  return HUES[n % HUES.length]
}

// The lead story: the listing marked `featured`, else one Loam made. The
// list carries no dates, so "newest" cannot be read from it; of Loam's own,
// the one with the highest version leads, then the first by name.
export function leadOf(entries) {
  const list = entries ?? []
  const featured = list.find(e => e.featured)
  if (featured) return featured
  const loam = list.filter(byLoam)
  if (!loam.length) return null
  const v = e => String(e.version ?? '0.0.0').split(/[.+-]/).slice(0, 3).map(n => parseInt(n, 10) || 0)
  return [...loam].sort((a, b) => { const [x, y] = [v(a), v(b)]; return (y[0] - x[0]) || (y[1] - x[1]) || (y[2] - x[2]) || a.name.localeCompare(b.name) })[0]
}

// A section per kind that has listings, in KINDS' order, each listing under
// its first kind, the lead story left out (it is already the page's top).
// A kind with nothing is no section at all.
export function sectionsOf(entries, lead = null, limit = 6) {
  return KINDS.map(kind => {
    const all = (entries ?? []).filter(e => kindOf(e) === kind && e.id !== lead?.id)
    return { kind, entries: all.slice(0, limit), total: all.length }
  }).filter(s => s.entries.length)
}

// The kinds the list has anything of: the chips the front page offers.
export const kindsListed = entries => KINDS.filter(k => (entries ?? []).some(e => (e.kind ?? []).includes(k)))

// What the Market's quiet line says under its masthead.
export function dateline(count, now = new Date()) {
  const day = formatDate(now, { weekday: 'long', day: 'numeric', month: 'long' })
  if (!count) return `${day} · Nothing listed yet`
  return `${day} · ${count} ${count === 1 ? 'thing' : 'things'} listed`
}

// How a section's tiles fall into rows (2026-11-18, the store): two wide,
// then three tall, again; one alone is one wide tile, and no row is left
// with a single small tile at its end (a lone last one joins the row before).
export function tileRows(count) {
  const rows = []
  for (let left = count, next = 2; left > 0; next = next === 2 ? 3 : 2) { const take = Math.min(next, left); rows.push(take); left -= take }
  if (rows.length > 1 && rows.at(-1) === 1) { rows.pop(); rows.push(...(rows.pop() === 3 ? [2, 2] : [3])) }
  return rows
}
// The fine line under a listing's Install: what it costs (nothing) and how
// far it reaches, from its own declaration.
export function fineLine(declaration, kind = null) {
  if (kind === 'theme' && !declaration?.page_code) return 'Free · changes only how Loam looks'
  const r = reachOf(declaration, kind)
  return `Free · ${r.text.charAt(0).toLowerCase()}${r.text.slice(1)}`
}
// How many listings their maker has in the Market (by the creator's name).
export function madeBy(entries, name) {
  const who = String(name ?? '').trim().toLowerCase()
  if (!who) return 0
  return (entries ?? []).filter(e => String(e.creator?.name ?? e.author ?? '').trim().toLowerCase() === who).length
}

// The request an agent finds in the composer: the person's own words, as
// something to build for them as their own plugin. Unsent.
export function buildRequest(q = '', kind = 'all') {
  const words = String(q ?? '').trim().replace(/\s+/g, ' ')
  const what = kind && kind !== 'all' ? (kind === 'extension' ? 'plugin' : KIND_ONE[kind].toLowerCase()) : 'plugin'
  if (words) return `Could you build me my own ${what} for this: “${words}”? I looked in the Market and nothing was quite right.`
  return `Could you build me my own ${what}? Here’s what I have in mind: `
}

// A listing's button, by what is installed: Get (a theme) or Install, and
// once it is here, In use or Use (a theme), Open (an app) or Installed.
// Agents and skills are listed and read, and not installed yet
// (lib/loam_plugins LISTED_ONLY): their button opens their page.
export const LISTED_ONLY = ['agent', 'skill']
export const listedOnly = entry => (entry?.kind ?? []).some(k => LISTED_ONLY.includes(k))
export function actionOf(entry, { installed = false, themeInUse = null, trying = null } = {}) {
  const kind = kindOf(entry)
  if (!installed && listedOnly(entry)) return { action: 'view', label: 'See it' }
  if (!installed) return { action: 'install', label: kind === 'theme' ? 'Get' : 'Install' }
  if (kind === 'theme') return themeInUse === entry.id ? { action: 'none', label: 'In use' } : { action: 'use', label: trying === entry.id ? 'Keep it' : 'Use' }
  if (kind === 'app') return { action: 'open', label: 'Open' }
  return { action: 'none', label: 'Installed' }
}

// Said on every install sheet but a plain theme's (2026-11-19): what a
// plugin runs, it runs with the whole server, as the agents do. The
// README's first screen says the same of Loam.
export const ROOT_LINE = 'Like your agents, a plugin has full control of this server: install what you trust. That is why Loam wants a server you use for nothing else.'

// What a plugin declares, in plain words, for the listing page and the
// install sheet: `{ glyph, text, sub }`. Read from the declaration an
// install's first call answers (lib/loam_plugins.declaration).
const PLACE = { 'core.message': 'the message bubble', 'core.thread-list': 'the thread list', 'core.composer': 'the composer', 'core.files.viewer': 'the Files viewer', 'core.files.editor': 'the Files editor', 'core.rail': 'the navigation rail', 'core.activity': 'Activity', 'core.apps': 'Apps', 'core.settings': 'Settings' }
export function plainTouches(declaration, kind = null, additionsOnly = false) {
  const d = declaration ?? {}, t = d.touches ?? {}, out = []
  const network = t.network ?? []
  for (const n of network) out.push({ glyph: 'out', text: n.host === 'browser-managed' ? 'Uses your browser’s own speech service' : `Talks to ${n.host}`, sub: n.why || '' })
  if (t.open_web) out.push({ glyph: 'out', text: 'Opens a door to the open web', sub: t.open_web })
  if (t.server) out.push({ glyph: 'server', text: 'Runs a small program on your server', sub: 'Started and watched by Loam; if it stops, only it stops.' })
  if (d.page_code) out.push({ glyph: 'page', text: 'Runs in this page', sub: 'Its code runs in your browser, beside Loam’s own.' })
  if (t.record === 'read') out.push({ glyph: 'record', text: 'Reads your threads', sub: '' })
  if (t.record === 'write') out.push({ glyph: 'record', text: 'Reads and changes your threads', sub: '' })
  if (t.record === 'bypass') out.push({ glyph: 'record', text: 'Keeps some threads out of the record', sub: 'By design: that is what it is for.' })
  if (t.memory === 'read') out.push({ glyph: 'memory', text: 'Reads what your agents keep for themselves', sub: '' })
  if (t.memory === 'write') out.push({ glyph: 'memory', text: 'Adds to what your agents keep for themselves', sub: '' })
  if (t.agents?.tools?.length) out.push({ glyph: 'tool', text: `Gives your agents ${t.agents.tools.length === 1 ? 'a new tool' : `${t.agents.tools.length} new tools`}`, sub: t.agents.tools.map(x => x.replace(/ \(.*\)$/, '')).join(', ') })
  if (t.agents?.prompt?.length) out.push({ glyph: 'tool', text: 'Adds instructions for agents', sub: t.agents.prompt.join(', ') })
  if (t.settings?.length) out.push({ glyph: 'tool', text: 'Adds settings', sub: t.settings.join(', ') })
  if (t.drift_check) out.push({ glyph: 'server', text: 'Runs a check on your server', sub: '' })
  for (const key of t.keys ?? []) {
    out.push({ glyph: 'key', text: `Uses your ${key.name} key${key.optional ? ' if you have one' : ''}`, sub: key.why || 'From API keys, in Settings › Connections.' })
  }
  if (t.secrets?.length) out.push({ glyph: 'key', text: 'Asks for a key of its own', sub: 'You paste it in its settings; agents never see it.' })
  if (t.vault?.length) out.push({ glyph: 'record', text: 'Writes files into your vault', sub: t.vault.join(', ') })
  if (t.schedule?.length) out.push({ glyph: 'clock', text: 'Runs on a schedule', sub: t.schedule.join(', ') })
  if (t.inbound?.length) out.push({ glyph: 'server', text: 'Listens for connections on your server', sub: t.inbound.join(', ') })
  if (t.engineer_env?.length || t.engineer_job?.length) out.push({ glyph: 'tool', text: 'Changes how your engineers work', sub: [...(t.engineer_env ?? []), ...(t.engineer_job ?? [])].join(', ') })
  for (const id of d.overrides ?? []) out.push({ glyph: 'page', text: `Replaces ${PLACE[id] ?? id}`, sub: '' })
  if (!additionsOnly && !network.length && !t.open_web && !t.server && !t.inbound?.length) {
    out.unshift(kind === 'theme' && !d.page_code
      ? { glyph: 'safe', text: 'Only colours and type', sub: 'No code, so it cannot change how anything works.' }
      : { glyph: 'safe', text: 'Sends nothing off your Loam', sub: '' })
  }
  return out
}

// The one line under a listing that says, at a glance, how far it reaches.
export function reachOf(declaration, kind = null) {
  const t = declaration?.touches ?? {}
  const n = (t.network ?? []).length
  if (n || t.open_web) return { tone: 'out', text: n > 1 ? `Talks to ${n} services` : 'Talks to one service' }
  if (t.server) return { tone: 'server', text: 'Stays on your server' }
  return { tone: 'safe', text: kind === 'theme' ? 'Only colours and type' : 'Stays on your Loam' }
}

// What a pasted link is, before the host reads it (2026-11-18, install from
// a link): `{ ok: true, repo }` for any shape the host accepts as GitHub
// (lib/loam_plugins.parse_source: a repository's address, owner/repo, a
// .git or git@ address, a /tree/, /blob/, /commit/ or release link, with or
// without a trailing slash or query), `{ ok: false }` for what plainly is
// not one, and null while there is too little to tell (still typing).
export function linkShape(text) {
  const s = String(text ?? '').trim().replace(/^<|>$/g, '').trim()
  if (!s) return null
  const repo = (owner, name) => ({ ok: true, repo: `${owner}/${name.replace(/\.git$/, '')}` })
  let m = /^(?:ssh:\/\/)?git@github\.com[:/]([\w.-]+)\/([\w.-]+?)\/?$/i.exec(s)
  if (m) return repo(m[1], m[2])
  m = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+)(?:[/?#].*)?$/i.exec(s)
  if (m) return repo(m[1], m[2])
  m = /^(?:https?:\/\/)?raw\.githubusercontent\.com\/([\w.-]+)\/([\w.-]+)\/.+$/i.exec(s)
  if (m) return repo(m[1], m[2])
  m = /^([\w-][\w.-]*)\/([\w.-]+)(?:\/(?:tree|blob|commit|releases)\/.*)?$/.exec(s)
  if (m && !/\.[a-z]{2,}$/i.test(m[1])) return repo(m[1], m[2])
  if (/^(?:https?:\/\/)?(?:www\.)?(?:g|gi|git|gith|githu|github|github\.(?:c|co|com)?)(?:\/[\w.-]*)?$/i.test(s) || /^https?:?\/?\/?$/i.test(s)) return null
  if (/\s/.test(s) || /^https?:\/\//i.test(s) || /^[\w-]+(?:\.[\w-]+)+(?:\/|$)/.test(s)) return { ok: false }
  return null
}
