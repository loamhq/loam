import { owner, isOwner } from './owner.js'
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
export function readAcceptedMessage(body) {
  if (body?.accepted !== true || typeof body.message?.id !== 'string' || typeof body.message?.text !== 'string') throw new Error('The runtime did not identify the accepted message.')
  return body.message
}
export function readThreadRecord(body, id) {
  if (!body?.thread || body.thread.id !== id || !Array.isArray(body.thread.members) || !Array.isArray(body.messages)
    || typeof body.has_more !== 'boolean' || !Object.hasOwn(body, 'turn')) {
    throw new Error('The thread could not be loaded in a format this page can read.')
  }
  const messages = body.messages.map(readThreadMessage)
  return messages.some((message, i) => message !== body.messages[i]) ? { ...body, messages } : body
}

const CREATION_LINE = /^Made .+ \(([a-z][a-z0-9-]*)\): their first thread is t-[0-9a-f]+\.?$/u
export function createdAgentId(message) {
  if (message?.kind !== 'system') return null
  if (typeof message.agent_created === 'string' && message.agent_created) return message.agent_created
  return typeof message.text === 'string' ? CREATION_LINE.exec(message.text.trim())?.[1] ?? null : null
}

function readThreadMessage(message) {
  const agent = createdAgentId(message)
  if (!agent) return message
  // Project the broker's legacy announcement onto the ID-only card, keeping
  // the original message's place in history and its reconnection cursor.
  const { id, kind, author, at } = message
  return { id, kind, author, at, agent_created: agent }
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
      else if (!open.liveReplies.has(turn.persona) && member?.working !== false && !parked) {
        const draft = draftFor(open, turn.persona, turn)
        if (turn.step) open.steps.set(turn.persona, { type: 'turn.step', persona: turn.persona, label: turn.step, steps: turn.steps ?? turn.did?.length ?? 1 })
        if (!open.progress.has(turn.persona) && Array.isArray(turn.did) && turn.did.length && !draft.segments?.length && !draft.text) {
          const parts = [{ did: turn.did.map((s) => ({ label: s.label })) }]
          open.progress.set(turn.persona, parts)
          draft.segments = parts
        }
      }
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
    // Only working:true can reserve their pre-prose reply position; when a
    // quiet notification turn finishes (working:false outside activeTurns),
    // clear any empty draft and transient step state it left behind.
    for (const member of open.thread.members) {
      if (member.working && !open.messages.some((m) => m.streaming && m.author === member.persona)) {
        draftFor(open, member.persona)
      } else if (member.working === false && !open.activeTurns.has(member.persona)) {
        open.typing.delete(member.persona)
        open.steps.delete(member.persona)
        open.progress.delete(member.persona)
        open.waiting.delete(member.persona)
        open.messages = open.messages.filter((m) => !(m.id === `draft:${member.persona}` && !m.text))
      }
    }
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
    const message = readThreadMessage({ ...e.message, ...(viewKey ? { viewKey } : {}), ...(parts?.length ? { segments: parts } : {}),
      ...(trailingDid?.length ? { trailingDid } : {}),
      ...(e.message.steps == null && previous?.steps ? { steps: previous.steps } : {}) })
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
    if (e.advanceCursor !== false && (!open.after || messageNumber(message.id) > messageNumber(open.after))) open.after = message.id
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
    if (p) {
      const existing = open.permissions.get(e.request_id)
      const after_message = existing && Object.hasOwn(existing, 'after_message')
        ? existing.after_message
        : (open.messages.findLast((m) => !m.streaming)?.id ?? null)
      open.permissions.set(e.request_id, { ...p, persona: e.persona, session_id: e.session_id, message_id: e.message_id, after_message })
    }
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

// Anchor unmatched permission and question cards where they were raised so
// messages sent while a card is open land below it, in view at the bottom.
// A card raised during a persona's still-streaming draft sits below that draft
// until another message lands after its anchor, then stays at the anchor.
export function placePermissions(messages, displayMessages, permissions, isVisible = () => true) {
  const beforeAll = [], afterById = new Map(), trailing = []
  if (!permissions?.length) return { beforeAll, afterById, trailing }
  const visible = displayMessages.filter((m) => isVisible(m))
  const visibleIndex = new Map(visible.map((m, i) => [m.id, i]))
  const firstDurable = messages.find((m) => !m.streaming && /\d+$/.test(String(m.id)))
  const addAfter = (id, p) => {
    let list = afterById.get(id)
    if (!list) { list = []; afterById.set(id, list) }
    list.push(p)
  }
  for (const p of permissions) {
    if (!Object.hasOwn(p, 'after_message') || p.after_message === undefined) {
      trailing.push(p)
      continue
    }
    if (p.after_message === null) {
      const first = visible[0]
      if (first?.streaming && first.author === p.persona) addAfter(first.id, p)
      else beforeAll.push(p)
      continue
    }
    let anchorId = null
    if (visibleIndex.has(p.after_message)) {
      anchorId = p.after_message
    } else {
      const idx = messages.findIndex((m) => m.id === p.after_message)
      if (idx >= 0) {
        for (let j = idx - 1; j >= 0; j--) {
          if (visibleIndex.has(messages[j].id)) { anchorId = messages[j].id; break }
        }
        if (anchorId === null) { beforeAll.push(p); continue }
      } else if (firstDurable && /\d+$/.test(String(p.after_message)) && messageNumber(p.after_message) < messageNumber(firstDurable.id)) {
        beforeAll.push(p)
        continue
      } else {
        trailing.push(p)
        continue
      }
    }
    const next = visible[visibleIndex.get(anchorId) + 1]
    if (next?.streaming && next.author === p.persona) anchorId = next.id
    addAfter(anchorId, p)
  }
  return { beforeAll, afterById, trailing }
}

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
