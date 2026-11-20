import { owner, isOwner } from './owner.js'
import { installation } from './installation.js'
import { createTranscript, apply } from './transcript.js'
import { fullStepSegments } from './thread-steps.js'
// Validate at the seam before enabling edits against a whole-file reading.
export function readThreadSummary(row) {
  if (typeof row?.id !== 'string' || !Array.isArray(row.members)) throw new Error('The runtime did not identify the thread in a format this page can read.')
  return row
}
export function readThreadList(body) {
  if (!Array.isArray(body?.threads) || !Array.isArray(body.agents)) throw new Error('Threads are not available in a format this page can read.')
  body.threads.forEach(readThreadSummary)
  return body
}
// The thread list's order: pinned first, then newest activity. Among pinned
// threads, General (by its id, never its title) leads. An unpinned General
// takes no place of its own.
export function compareThreads(a, b, { pinned = true } = {}) {
  if (pinned) {
    const byPin = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
    if (byPin) return byPin
    if (a.pinned) { const byGeneral = Number(b.id === 'general') - Number(a.id === 'general'); if (byGeneral) return byGeneral }
  }
  return String(b.last?.at ?? b.created ?? '').localeCompare(String(a.last?.at ?? a.created ?? ''))
}
export function readThreadBoard(board) {
  if (typeof board?.markdown !== 'string' || typeof board?.path !== 'string') throw new Error('The board could not be read.')
  return board
}
export function readAcceptedMessage(body) {
  if (body?.accepted !== true || typeof body.message?.id !== 'string' || typeof body.message?.text !== 'string') throw new Error('The runtime did not identify the accepted message.')
  return body.message
}
export function readThreadRecord(body, id) {
  if (!body?.thread || body.thread.id !== id || !Array.isArray(body.thread.members) || !Array.isArray(body.messages)
    || typeof body.has_more !== 'boolean' || !Object.hasOwn(body, 'turn')
    || typeof body.board?.markdown !== 'string' || typeof body.board?.path !== 'string') {
    throw new Error('The thread could not be loaded in a format this page can read.')
  }
  return body
}

// A board action changes only the requested bytes. Unknown metadata, spacing,
// line endings, prose and fenced examples survive whole-file saves.
export function boardLines(markdown) {
  let fence = null
  const front = /^(?:\uFEFF)?---[ \t]*\r?\n(?:[\s\S]*?\r?\n)?(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/.exec(markdown)?.[0] ?? ''
  let offset = 0, firstTitle = true
  return String(markdown ?? '').split(/(?<=\n)/).map((raw, index) => {
    const line = raw.replace(/\r?\n$/, '')
    const hidden = offset < front.length
    offset += raw.length
    if (hidden) return { raw, index, line, hidden }
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
    if (marker) {
      if (!fence) fence = { char: marker[1][0], length: marker[1].length }
      else if (marker[1][0] === fence.char && marker[1].length >= fence.length && !marker[2].trim()) fence = null
      return { raw, index, line, fenced: true }
    }
    const item = !fence && /^(\s*[-*+]\s+)(?:\[([ xX])\]\s+)?(.*?)(\s*<!--([^<>]*)-->\s*)?$/.exec(line)
    if (!item) {
      const heading = !fence && /^ {0,3}(#{1,3})[ \t]+(.+?)[ \t]*$/.exec(line)
      if (heading?.[1] === '#' && firstTitle) {
        firstTitle = false
        return { raw, index, line, title: true }
      }
      return { raw, index, line, fenced: Boolean(fence), ...(heading ? { heading: heading[2].replace(/[ \t]+#+[ \t]*$/, '').trim(), level: heading[1].length } : {}) }
    }
    const parts = (item[5] ?? '').split('·').map((s) => s.trim())
    const dated = /^(\d{4}-\d{2}-\d{2})([ \t]+·[ \t]+)(?:\*\*([^*]+)\*\*[ \t]+·[ \t]+)?/.exec(item[3])
    const metadata = item[4] ?? ''
    const text = item[3].slice(dated?.[0].length ?? 0)
    const prefix = line.slice(0, line.length - metadata.length - text.length)
    return { raw, index, line, item: true, marker: prefix.slice(0, prefix.length - (dated?.[0].length ?? 0)),
      prefix, date: dated?.[1], checked: item[2] === undefined ? null : item[2].toLowerCase() === 'x',
      text, author: parts.find((s) => s && s !== 'new' && !s.startsWith('until ')) ?? dated?.[3],
      until: parts.find((s) => /^until \d{4}-\d{2}-\d{2}$/.test(s))?.slice(6),
      unseen: parts.includes('new'), metadata }
  })
}
export function editBoard(markdown, index, action) {
  const lines = boardLines(markdown)
  const item = lines[index]
  if (!item?.item) return markdown
  if (action === 'remove') lines[index].raw = ''
  if (action === 'tick' && item.checked !== null) lines[index].raw = item.raw.replace(/^(\s*[-*+]\s+\[)[ xX](\])/, `$1${item.checked ? ' ' : 'x'}$2`)
  if (action === 'seen') lines[index].raw = item.raw.replace(/<!--([^<>]*)-->(?=\s*$)/, (_, content) => {
    const fields = content.split('·')
    const at = fields.findIndex((s) => s.trim() === 'new')
    if (at < 0) return `<!--${content}-->`
    fields.splice(at, 1)
    return `<!--${fields.length ? fields.join('·').replace(/\s*$/, ' ') : ' '}-->`
  })
  return lines.map((l) => l.raw).join('')
}

// Prose is rendered together so a fenced example or table stays markdown.
export function boardBlocks(text) {
  return blocksOf(boardLines(text))
}
function blocksOf(lines) {
  const blocks = []
  const copies = new Map()
  for (const line of lines) {
    if (line.hidden || line.title || (!line.fenced && /^\s*---\s*$/.test(line.line))) continue
    if (line.item) {
      const n = copies.get(line.text) ?? 0
      copies.set(line.text, n + 1)
      blocks.push({ ...line, key: `item:${line.text}:${n}` })
    } else if (blocks.at(-1)?.prose) blocks.at(-1).text += line.raw
    else blocks.push({ prose: true, text: line.raw, key: `prose:${line.index}` })
  }
  return blocks
}
// Heading identities survive insertions in earlier lists. Repeated names remain
// separate destinations, and headings in fenced examples never split a list.
export function boardSections(markdown) {
  const lines = boardLines(markdown), copies = new Map()
  const sections = [{ key: 'top', name: 'Board', start: 0, end: lines.length, lines: [] }]
  for (const line of lines) {
    if (line.heading) {
      sections.at(-1).end = line.index
      const n = copies.get(line.heading) ?? 0; copies.set(line.heading, n + 1)
      sections.push({ key: `heading:${line.heading}:${n}`, name: line.heading, level: line.level, start: line.index + 1, end: lines.length, lines: [] })
    } else sections.at(-1).lines.push(line)
  }
  return sections.map((s) => ({ ...s, blocks: blocksOf(s.lines) }))
}
function boardInsert(markdown, index, raw) {
  const lines = boardLines(markdown), eol = markdown.includes('\r\n') ? '\r\n' : '\n'
  const before = lines.slice(0, index).map((l) => l.raw).join(''), after = lines.slice(index).map((l) => l.raw).join('')
  return before + (before && !before.endsWith('\n') ? eol : '') + raw + (after && !raw.endsWith('\n') ? eol : '') + after
}
// Move the original line, never reconstruct its checkbox, marker or metadata.
// `before` is an original source-line index; null means the destination's end.
export function moveBoardItem(markdown, index, destination, before = null) {
  const lines = boardLines(markdown), item = lines[index], section = boardSections(markdown).find((s) => s.key === destination)
  if (!item?.item || !section) return markdown
  const target = before ?? boardItemEnd(section)
  if (!Number.isInteger(target) || target < section.start || target > section.end) return markdown
  if (target === index || target === index + 1) return markdown
  const remaining = lines.filter((l) => l.index !== index).map((l) => l.raw).join('')
  return boardInsert(remaining, target - (index < target ? 1 : 0), item.raw)
}
export function boardItemEnd(section) {
  const last = section.lines.findLast((l) => l.item)
  return last ? last.index + 1 : section.key === 'top' ? section.end : section.start
}
// Display-only soft wrapping. Keep the quote marker and leave examples intact;
// board writes always use the original markdown.
export function reflowBoardQuotes(text) {
  const out = []
  let fence = null, previousQuote = false
  for (const line of String(text).split(/\r?\n/)) {
    const marker = /^\s*(?:>\s*)?(`{3,}|~{3,})/.exec(line)
    if (marker) { fence = fence === marker[1][0] ? null : fence ?? marker[1][0]; previousQuote = false; out.push(line); continue }
    const quote = !fence && /^\s*>[ \t]+(.+)$/.exec(line)
    const prose = quote && !/^(?:#|[-*+]\s|\d+[.)]\s)/.test(quote[1])
    if (prose && previousQuote) out[out.length - 1] += ` ${quote[1].trim()}`
    else out.push(line)
    previousQuote = Boolean(prose)
  }
  return out.join('\n')
}
export function markBoardSeen(markdown) {
  return boardLines(markdown).filter((l) => l.unseen).reduce((text, line) => editBoard(text, line.index, 'seen'), markdown)
}
export function addBoardItem(markdown, text, days = 0, now = new Date(), path = '', destination = null) {
  const clean = text.trim().replace(/\r?\n/g, ' ')
  if (!clean) return markdown
  const until = new Date(now); until.setUTCDate(until.getUTCDate() + Number(days))
  const eol = markdown.includes('\r\n') ? '\r\n' : '\n'
  const section = destination === null ? null : boardSections(markdown).find((s) => s.key === destination)
  if (destination !== null && !section) return markdown
  const marker = path === installation().generalBoard && !Number(days) ? '- ' : '- [ ] '
  const author = owner().name.replace(/-->|[\r\n]/g, ' ')
  const expiry = Number(days) ? ` · until ${until.toISOString().slice(0, 10)}` : ''
  const raw = `${marker}${now.toISOString().slice(0, 10)} · ${clean} <!-- ${author}${expiry} -->${eol}`
  return boardInsert(markdown, section ? boardItemEnd(section) : boardLines(markdown).length, raw)
}

// View IDs stay unique across cached visits and new turns in this page.
let replySerial = 0
// New runtimes state every active turn; an explicit empty array outranks the
// legacy single-turn field. Keep the host's start order, including on reconnect.
export function threadTurns(record) {
  return Array.isArray(record.turns) ? record.turns : record.turn?.persona ? [record.turn] : []
}
function draftFor(open, persona, turn = {}) {
  let draft = open.messages.find((m) => m.id === `draft:${persona}`)
  if (!draft) {
    const member = open.thread.members.find((m) => m.persona === persona)
    draft = { id: `draft:${persona}`, viewKey: `reply:${persona}:${++replySerial}`, author: persona, kind: 'message', text: '', segments: [], streaming: true,
      engine: turn.engine ?? member?.engine, model: Object.hasOwn(turn, 'model') ? turn.model : member?.model }
    open.messages.push(draft)
  }
  return draft
}

// The host owns completed message IDs. A reply keeps its view identity when
// that ID arrives; only message.done advances reconnection.
export function applyThreadEvent(open, e) {
  open.steps ??= new Map()
  open.progress ??= new Map()
  open.waiting ??= new Map()
  open.activeTurns ??= new Set()
  open.liveReplies ??= new Map()
  if (['thread.snapshot', 'members'].includes(e.type) && Array.isArray(e.members)) {
    // This is the whole current array, including removals and cleared counts.
    open.thread = { ...open.thread, members: e.members }
    for (const key of ['background', 'working']) {
      if (Number.isInteger(e[key])) open.thread[key] = e[key]
      else delete open.thread[key]
    }
  }
  if (e.type === 'thread.snapshot' && Array.isArray(e.marks)) open.marks = e.marks
  if (e.type === 'turn.mark') {
    const persona = e.persona ?? null
    open.marks = (open.marks ?? []).filter((m) => (m.persona ?? null) !== persona)
    if (e.text) open.marks.push({ persona, text: e.text, at: e.at })
  }
  if (e.type === 'thread.snapshot') {
    const previous = open.turns ?? [], turns = threadTurns(e)
    const continuing = new Set(turns.filter((turn) => previous.some((old) => old.persona === turn.persona
      && (!old.started_at || !turn.started_at || old.started_at === turn.started_at))).map((t) => t.persona))
    if (Array.isArray(e.members)) for (const member of e.members) {
      // Notifications can generate outside the registered thread turn. The
      // member's working reading is authoritative for those drafts too.
      if (member.working && !turns.some((t) => t.persona === member.persona)) continuing.add(member.persona)
    }
    open.activeTurns.clear()
    open.typing.clear(); open.steps.clear(); open.waiting.clear()
    for (const id of open.progress.keys()) if (!continuing.has(id)) open.progress.delete(id)
    for (const id of open.liveReplies.keys()) if (!continuing.has(id)) open.liveReplies.delete(id)
    open.turns = turns
    // A reconnect snapshot states liveness, not replacement draft text. Keep
    // each continuing member's prose; live replies stay below the record.
    open.messages = open.messages.filter((m) => !m.streaming || continuing.has(m.author))
    for (const turn of turns) {
      open.activeTurns.add(turn.persona)
      open.typing.add(turn.persona)
      if (turn.waiting) open.waiting.set(turn.persona, turn.waiting)
      const member = open.thread.members.find((m) => m.persona === turn.persona)
      const parked = (member?.working === false && member.background > 0) || (member?.working == null && turn.on_background)
      const reply = parked
        && open.messages.findLast((m) => !m.streaming && m.author === turn.persona && m.kind !== 'system')
      if (reply && !open.messages.some((m) => m.streaming && m.author === turn.persona)) open.liveReplies.set(turn.persona, reply.id)
      else if (!open.liveReplies.has(turn.persona) && member?.working !== false && !parked) draftFor(open, turn.persona, turn)
    }
    if (Array.isArray(e.pending)) {
      const pending = new Set(e.pending.map((p) => p.request_id))
      for (const id of open.permissions.keys()) if (!pending.has(id)) open.permissions.delete(id)
      for (const p of e.pending) applyThreadEvent(open, { ...p, type: 'broker.permission_request' })
    }
  }
  if (e.type === 'thread.changed' && e.summary?.id === open.thread.id) open.thread = e.summary
  if (['thread.snapshot', 'members'].includes(e.type)) {
    // Notification turns may start from a member reading without turn.started.
    // Only working:true can reserve their pre-prose reply position.
    for (const member of open.thread.members) if (member.working
      && !open.messages.some((m) => m.streaming && m.author === member.persona)) draftFor(open, member.persona)
  }
  if (e.type === 'turn.started') {
    if (open.marks?.length) open.marks = open.marks.filter((m) => m.persona !== e.persona)
    open.liveReplies.delete(e.persona)
    open.turns = [...(open.turns ?? []).filter((t) => t.persona !== e.persona), e]
    open.activeTurns.add(e.persona)
    open.typing.add(e.persona); open.steps.delete(e.persona); open.progress.delete(e.persona); open.waiting.delete(e.persona)
    open.messages = open.messages.filter((m) => m.id !== `draft:${e.persona}`)
    draftFor(open, e.persona, e)
  }
  if (e.type === 'turn.finished') {
    open.turns = (open.turns ?? []).filter((t) => t.persona !== e.persona)
    open.activeTurns.delete(e.persona)
    open.typing.delete(e.persona); open.steps.delete(e.persona); open.progress.delete(e.persona); open.waiting.delete(e.persona)
    open.messages = open.messages.filter((m) => m.id !== `draft:${e.persona}`)
  }
  if (e.type === 'turn.waiting') { open.typing.add(e.persona); open.waiting.set(e.persona, e.text) }
  if (e.type === 'turn.step') {
    open.waiting.delete(e.persona)
    open.typing.add(e.persona); open.steps.set(e.persona, e)
    // A background wait belongs to the header task list, never a reply step.
    if (e.waiting) return
    const parts = open.progress.get(e.persona) ?? []
    const draft = draftFor(open, e.persona)
    // A subagent/background notification does not end the main speaker's
    // draft. Keep its work beside the growing prose until a top-level step.
    if (draft?.text && (e.waiting || /^↳/.test(e.label ?? ''))) {
      draft.trailingDid ??= []; draft.trailingDid.push({ label: e.label })
      return
    }
    if (draft?.text) parts.push({ text: draft.text })
    if (draft?.trailingDid?.length) parts.push({ did: draft.trailingDid })
    if (parts.at(-1)?.did) parts.at(-1).did.push({ label: e.label })
    else parts.push({ did: [{ label: e.label }] })
    open.progress.set(e.persona, parts)
    Object.assign(draft, { text: '', segments: parts }); delete draft.trailingDid
  }
  if (e.type === 'message.delta') {
    open.typing.add(e.persona); open.steps.delete(e.persona); open.waiting.delete(e.persona)
    const draft = draftFor(open, e.persona), id = draft.id, at = open.messages.indexOf(draft)
    // `reply_to` rides on the delta once the reply's opening named it: the
    // quote shows while the words come in, in the place it keeps when posted.
    const message = { ...draft, id, author: e.persona, text: e.draft ?? e.text ?? '', kind: 'message', did: [], segments: open.progress.get(e.persona) ?? [],
      ...(typeof e.reply_to === 'string' ? { reply_to: e.reply_to } : {}),
      ...(at >= 0 && open.messages[at].trailingDid ? { trailingDid: open.messages[at].trailingDid } : {}), streaming: true }
    if (at < 0) open.messages.push(message); else open.messages[at] = message
  }
  if (e.type === 'message.done' && e.message?.id) {
    const previous = open.messages.find((m) => m.id === e.message.id)
    const turn = open.turns?.find((t) => t.persona === e.message.author)
    const replay = Boolean(previous) || Date.parse(e.message.at) < Date.parse(turn?.started_at)
    const parts = e.message.segments ?? (replay ? previous?.segments : open.progress.get(e.message.author))
    const draft = !replay && open.messages.find((m) => m.id === `draft:${e.message.author}`)
    const viewKey = previous?.viewKey ?? draft?.viewKey
    const trailingDid = e.message.segments == null ? (previous?.trailingDid ?? draft?.trailingDid) : null
    const message = { ...e.message, ...(viewKey ? { viewKey } : {}), ...(parts?.length ? { segments: parts } : {}),
      ...(trailingDid?.length ? { trailingDid } : {}),
      ...(e.message.steps == null && previous?.steps ? { steps: previous.steps } : {}) }
    const draftAt = replay ? -1 : open.messages.findIndex((m) => m.id === `draft:${message.author}`)
    if (!replay) {
      open.progress.delete(message.author)
      const member = open.thread.members.find((m) => m.persona === message.author)
      if (open.activeTurns.has(message.author) || open.typing.has(message.author) || member?.working || member?.background > 0) open.liveReplies.set(message.author, message.id)
      // The finished reply takes its posting place, not the draft's (owner,
      // 2026-11-16): whatever was posted while it worked stays above it.
      if (draftAt >= 0) open.messages.splice(draftAt, 1)
      if (!open.activeTurns.has(message.author)) open.typing.delete(message.author)
      open.steps.delete(message.author); open.waiting.delete(message.author)
    }
    const at = open.messages.findIndex((m) => m.id === message.id)
    if (at >= 0) open.messages[at] = message
    else if (message.kind === 'system' && message.after) {
      // Branch notices belong beneath their source, even when that source is
      // above the current tail. A later history read supplies offscreen ones.
      let anchor = open.messages.findIndex((m) => m.id === message.after)
      if (anchor >= 0) {
        while (open.messages[anchor + 1]?.after === message.after) anchor++
        open.messages.splice(anchor + 1, 0, message)
      }
    } else settle(open.messages, message)
    if (!open.after || messageNumber(message.id) > messageNumber(open.after)) open.after = message.id
    // The accepted own message may reach SSE before its HTTP response.
    if (open.pending?.text === message.text && isOwner(message.author)) open.pending = null
  }
  if (e.type === 'member.joined' && !open.thread.members.some((m) => m.persona === e.persona)) {
    open.thread.members.push({ persona: e.persona, engine: e.engine ?? null })
  }
  if (e.type === 'broker.permission_request') {
    // Use the shared event validation and normalization, including
    // malformed-question fallback and session-only permission suggestions.
    open.permissionState ??= createTranscript()
    apply(open.permissionState, { ...e, seq: undefined })
    const p = open.permissionState.permissions.find((p) => p.request_id === e.request_id)
    if (p) open.permissions.set(e.request_id, { ...p, persona: e.persona, session_id: e.session_id, message_id: e.message_id })
  }
  if (e.type === 'broker.permission_resolved') {
    open.permissionState ??= createTranscript()
    apply(open.permissionState, { ...e, seq: undefined })
    open.permissions.delete(e.request_id)
  }
}
// Durable messages stand in posting order and live replies follow them all:
// a new one goes after the last durable message posted before it, ahead of
// any draft still streaming. The thread read gives the same order. An id
// with no number (a fixture's, a local one) has no posting place: it goes last.
// Branch notices keep their anchor's place so intervening settlements cannot
// split a notice from the message it branched out of.
function settle(messages, message) {
  const n = /\d+$/.test(String(message.id)) ? messageNumber(message.id) : Infinity
  let at = messages.length
  while (at > 0 && (messages[at - 1].streaming || messageNumber(messages[at - 1].after ?? messages[at - 1].id) > n)) at--
  messages.splice(at, 0, message)
}
// Where a message being sent is drawn: after the settled record, ahead of
// the live replies, where its durable copy will stand.
export function withPending(messages, pending) {
  if (!pending) return messages
  let at = messages.length
  while (at > 0 && messages[at - 1].streaming) at--
  return [...messages.slice(0, at), ...[].concat(pending), ...messages.slice(at)]
}
// A turn's mark (2026-11-19): "waiting for room", "X is archived", "X's turn
// failed: ...". Host state on the member's turn, never a message in the
// record; drawn where it happened, as Loam's lines were, and gone when that
// member's next turn starts (or, for the thread's own, when you next write).
export function withMarks(messages, marks) {
  if (!Array.isArray(marks) || !marks.length) return messages
  const out = [...messages]
  for (const mark of [...marks].sort((a, b) => String(a.at).localeCompare(String(b.at)))) {
    if (!mark?.text) continue
    let at = out.findIndex((m) => m.streaming || (m.at && String(m.at) > String(mark.at)))
    if (at < 0) at = out.length
    out.splice(at, 0, { id: `mark:${mark.persona ?? ''}`, kind: 'system', author: 'system', mark: true, persona: mark.persona ?? null, text: mark.text, at: mark.at })
  }
  return out
}
export function messageNumber(id) { return Number(String(id).match(/(\d+)$/)?.[1] ?? 0) }

export function recentMembers(thread, messages = []) {
  const spoke = new Map()
  for (const message of messages) if (message.kind !== 'system') spoke.set(message.author, message.at ?? message.id)
  if (thread.last?.author && !spoke.has(thread.last.author)) spoke.set(thread.last.author, thread.last.at ?? '')
  const lastSpoke = (member) => [member.last_spoke_at ?? '', spoke.get(member.persona) ?? ''].sort().at(-1)
  const members = [...thread.members].sort((a, b) => {
    if (a.persona === thread.default) return -1
    if (b.persona === thread.default) return 1
    return String(lastSpoke(b)).localeCompare(String(lastSpoke(a)))
  })
  // Fill the stack even before the first durable reply. Recent speakers lead;
  // quiet members fill spare places, so +N only means members beyond three.
  return members.slice(0, 3)
}

export function firstMessageTitle(text) {
  const clean = text.trim().replace(/^(?:@[\p{L}\p{N}_-]+\s*)+/u, '').trim()
  if (!clean) return 'New thread'
  return clean.length > 60 ? `${clean.slice(0, 59).trimEnd()}…` : clean
}

// A reply's `↩ m12` opening is Loam's linkage, shown as the quote above the
// reply and never as words (2026-11-17). Only an opening naming the message's
// own `reply_to` comes off; every other id, arrow and word stays as written.
const REPLY_MARK = /^\s*(?:↩|<-)\s*(m\d+)\b[ \t]*[:.,—-]?[ \t]*\n?/
export function withoutReplyMark(text, replyTo) {
  const match = replyTo && typeof text === 'string' ? text.match(REPLY_MARK) : null
  return match && match[1] === replyTo ? text.slice(match[0].length).trimStart() : text
}
function unmarked(parts, replyTo) {
  const at = replyTo ? parts.findIndex((part) => part.text?.trim()) : -1
  if (at < 0) return parts
  const text = withoutReplyMark(parts[at].text, replyTo)
  if (text === parts[at].text) return parts
  return parts.map((part, index) => index === at ? { ...part, text } : part).filter((part, index) => index !== at || part.text.trim())
}

export function messageSegments(message) {
  return unmarked(segmentsOf(message), message.reply_to)
}
function segmentsOf(message) {
  if (message.preset === true) return message.text ? [{ text: message.text }] : []
  if (Array.isArray(message.steps)) {
    const segments = fullStepSegments(message.steps, message.model)
    if (segments.some((part) => part.text?.trim())) return segments
    return [...segments, ...(message.text ? [{ text: message.text }] : [])]
  }
  if (message.segments?.length || message.trailingDid?.length) return [...(message.segments ?? []), ...(message.text ? [{ text: message.text }] : []),
    ...(message.trailingDid?.length ? [{ did: message.trailingDid, concurrent: true }] : [])]
  const parts = []
  for (const step of message.did ?? []) {
    if (/^Said: /.test(step.label)) parts.push({ text: step.label.slice(6) })
    else if (parts.at(-1)?.did) parts.at(-1).did.push(step)
    else parts.push({ did: [step] })
  }
  if (message.text) parts.push({ text: message.text })
  return parts
}

// A view only: durable messages and reconnect IDs are left intact. Group only
// joins attributable to mentions in the same preceding human message.
export function groupJoinNotices(messages, agents) {
  const out = [], groups = new Map()
  let source = null
  for (const message of messages) {
    if (message.kind !== 'system' && isOwner(message.author)) source = message
    const match = message.kind === 'system' && String(message.text ?? (message.member_joined ? `${message.actor ?? 'You'} brought in ${message.member_joined}` : '')).replace(/\[\[([^\]]+)\]\]/g, '$1').match(/^(.+?) brought in (.+?)\.?$/u)
    const joined = match && agents.find((a) => a.id.toLowerCase() === match[2].toLowerCase() || a.name.toLowerCase() === match[2].toLowerCase())
    const tags = new Set((source?.text?.match(/(?<![\w@])@[\p{L}\p{N}_-]+/gu) ?? []).map((tag) => tag.slice(1).toLowerCase()))
    if (!joined || !source || !tags.has(joined.id.toLowerCase())) { out.push(message); continue }
    const key = `${source.id}:${match[1]}`
    let group = groups.get(key)
    if (!group) { group = { ...message, joins: { actor: match[1], names: [] } }; groups.set(key, group); out.push(group) }
    group.joins.names.push(joined.id)
  }
  return out
}

export function messageRecipients(thread, text, agents) {
  const tags = new Set((text.match(/(?<![\w@])@[\p{L}\p{N}_-]+/gu) ?? []).map((tag) => tag.slice(1).toLowerCase()))
  if (!tags.size) return thread.members.filter((m) => m.persona === thread.default)
  const named = agents.filter((a) => tags.has(a.id.toLowerCase())).map((a) => thread.members.find((m) => m.persona === a.id)
    ?? { persona: a.id, engine: null, model: null })
  if (tags.has('all') || tags.has('everyone')) {
    for (const member of thread.members) if (!named.some((m) => m.persona === member.persona)) named.push(member)
    if (thread.default && !named.some((m) => m.persona === thread.default)) named.push({ persona: thread.default, engine: null, model: null })
  }
  return named
}
