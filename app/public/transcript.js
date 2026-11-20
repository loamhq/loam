// The seam.
//
// One place turns a session's events into the shape the page draws. The events
// are the coding CLI's own — `system`, `assistant`, `user`, `stream_event`,
// `result` — plus the broker's messages, which are prefixed `broker.` and are all
// flat: every field sits beside `type` and `seq`, nothing is nested. Nothing
// outside this file knows what a `stream_event` is, and nothing here draws
// anything. A second agent, speaking a different dialect, is a second reducer
// beside this one and no change to the page.
//
// The set of event types grows whenever the agent CLI grows, and the broker is
// not a filter, so **an event this file does not recognise is ignored**. It is
// not an error and it is not a line on the page.
//
// It is a reducer over a stream that may repeat itself: the broker would
// rather send an event twice than drop it, so every logged event carries a
// `seq` and applying it twice must change nothing. `seq` does not start at 1
// for a resumed session — it continues the old numbering, because it is the
// same session.
//
// **`broker.session` is a snapshot rather than a logged event.** It
// is a snapshot of the session's record: never logged, never numbered, and its
// `record_seq` is the number of the last event in the log — where the session
// is, not where this page got to. So it is taken before any of the numbering
// below: it is never deduplicated by number and it never moves the position
// this page reconnects at. It arrives first on every stream, and again each
// time a turn record is written, which is where a turn's cost comes from.
// `broker.write_failed` is also unnumbered: a live-only notice about a failed
// log write. It never changes the reconnect position.
//
// This module runs in the browser and under `node --test`, unchanged. The
// tests feed it recorded streams with no agent present.

// The one thing it takes from elsewhere: how a list of attachments is read, so
// a message's files are read the same way whether they arrive on the record or
// come back from an upload. `attach.js` has no DOM and no network either.
import { attachmentList } from './attach.js'
import { SETTLED_STATUSES, tokenWords as tokens } from './sessions.js'

export function createTranscript() {
  return {
    session: null, // the broker's session record, as it last said it
    init: null, // the CLI's own `system`/`init`: model, tools, working directory
    timeline: [], // what the page draws, in order
    permissions: [], // asked and not yet answered
    notices: [], // a turn being retried — the banner
    // `{ code, seq }` once the process has ended, and `null` again the moment
    // anything later than it arrives — see `apply()`. An exit is the end of the
    // log until something comes after it, and a resumed session says so by
    // saying something.
    exit: null,
    // Whether this page has read a `broker.exit` anywhere — unlike `exit`, never
    // cleared by what comes after. A page of history prepended above it is all
    // older than that exit, so a call in it still without a result died with a
    // process. See `prependEvents()`.
    _exited: false,
    // What the session has going on in the background: the count and the
    // descriptions the host last named, from `broker.background_tasks`. The
    // count a page draws is the record's `background_tasks`, which every
    // snapshot carries; these are the names behind it.
    tasks: [],
    // The latest compaction this page has read: `{ seq, trigger, before, after }`
    // from `broker.compaction`, or `null`. It is what the context reads as
    // between a compaction and the next real turn — see `contextNow()`.
    compaction: null,
    lastSeq: null, // what to ask the broker for on reconnect
    // Where the window this page opened at begins, and how much is above it.
    // Both are the host's, both are on the snapshot of a windowed answer only,
    // and neither is durable — see `readWindow()` below.
    window: { firstSeq: null, turnsBefore: null },
    seen: new Set(), // seq values already applied
    _tools: new Map(), // tool_use_id -> the tool block, at any depth
    _messages: new Map(), // message id -> the assistant node
    _open: new Map(), // parent tool id (or '') -> the message being written now
    _placedTools: new Map(), // tool block -> the one place it is drawn
    _results: [], // this page's turn lines, in order, so the record can price them
    _writeFailure: null, // the unsaved reply's notice while its turn is in flight
    _unrecordedSaveNotice: null, // the live line awaiting its durable turn record
    _writeFailures: new Map(), // live from_seq -> notice, for duplicate delivery
    _emptySaveAnchors: [], // durable failed turns when the log is entirely empty
    _resolved: new Set(), // permission request ids that have been answered
    // request id -> what it asked, kept after the card goes, so the line it
    // leaves can name the tool and its target whatever closed it.
    _requests: new Map(),
    _lines: new Map(), // request id -> the line it left in the thread
    // The latest record's `pending_permissions`, as ids, and the `record_seq` it
    // was true at — see `brokerSession()`. `null` until a record has said.
    _pending: null,
    _asked: new Map(), // request id -> the questions it asked, for the answer's line
  }
}

export function apply(state, event) {
  if (!event || typeof event !== 'object') return state

  // The snapshot, first and apart: no `seq`, no place in the log, no say in
  // where this page reconnects. Even one that arrived carrying a number would
  // not be read as one here.
  if (event.type === 'broker.session') return priceFromSnapshot(brokerSession(state, event))
  // A live-only notice, never a log position, even if a future host adds fields.
  if (event.type === 'broker.write_failed') return writeFailed(state, event)

  const seq = event.seq
  if (seq !== undefined && seq !== null) {
    if (state.seen.has(seq)) return state // said twice; say it once
    markSeen(state, seq)
  }

  // **An exit is only the end of the log until something comes after it.** A
  // session brought back under its own id keeps its old numbering, so the
  // events of the new process arrive above the `broker.exit` of the old one —
  // and a page that kept the exit forever told a person their chat was not
  // running while the agent was answering them. Anything later than the exit
  // is the process being alive again, and the exit line stays in the timeline
  // where it belongs: it is history, not a state.
  if (state.exit && event.type !== 'broker.exit' && (state.exit.seq === null || seq === undefined || seq === null || seq > state.exit.seq)) {
    state.exit = null
  }

  // A turn line is priced from the record, and whether the record can price it
  // depends on how far this page has read — which every event changes.
  dispatch(state, event)
  // Keep the live notice below the reply as new blocks arrive.
  if (state._writeFailure) placeSaveNotice(state, state._writeFailure)
  return priceFromSnapshot(state)
}

function dispatch(state, event) {
  switch (event.type) {
    case 'broker.user_message':
      return brokerUserMessage(state, event)
    case 'broker.permission_request':
      return permissionRequest(state, event)
    case 'broker.permission_resolved':
      return permissionResolved(state, event)
    case 'broker.background_tasks':
      return backgroundTasks(state, event)
    case 'broker.exit':
      return brokerExit(state, event)
    case 'broker.compaction':
      return brokerCompaction(state, event)
    case 'system':
      return system(state, event)
    case 'stream_event':
      return streamEvent(state, event)
    case 'assistant':
      return assistant(state, event)
    case 'user':
      return user(state, event)
    case 'result':
      return result(state, event)
    default:
      // A type this app has not met — `rate_limit_event` today, something else
      // next month. The broker passes everything through; this reducer is
      // where the page's vocabulary stops, so it ends here silently.
      return state
  }
}

export function applyAll(state, events) {
  for (const event of events) apply(state, event)
  return state
}

// One page further up the conversation: the turns below the window this page
// already has, in order, going in above everything it is showing.
//
// It is a second reduction rather than a second code path. The page of history
// is reduced into a transcript of its own — the same reducer, the same rules —
// and what comes out of it is pushed onto the front of this one. So a tool call
// and the result that came back to it are paired inside the page they happened
// in, and nothing about drawing a line changes because of where it was fetched.
//
// Three things it deliberately does not do. It does not move `lastSeq`, which
// is where this page reconnects and is always the *newest* event seen, never an
// older one. It does not take the record off the page's leading snapshot —
// only the two window fields, which say where the next page begins. And it does
// not carry over permissions or retry banners: a question asked in a turn from
// last week was answered in it, and a banner is about a turn in flight.
export function prependEvents(state, events) {
  const older = createTranscript()
  // What the record says is pending is as true of history as of now, so a
  // request in an older page that the record no longer holds is drawn as the
  // line it left and never as a card.
  older._pending = state._pending
  for (const event of Array.isArray(events) ? events : []) {
    if (!event || typeof event !== 'object') continue
    if (event.type === 'broker.session') {
      readWindow(state, event)
      continue
    }
    const seq = event.seq
    if (seq !== undefined && seq !== null) {
      if (state.seen.has(seq)) continue
      // Seen, so a reconnect that replays over this page draws it once — but
      // not through `markSeen()`, which would let an older number claim to be
      // the position this page has read to.
      state.seen.add(seq)
    }
    apply(older, event)
  }
  // A request whose closing event this page already read below has its line
  // there; the older page's provisional line for it would be a second one. What
  // it asked fills in the line that stays.
  const lines = older.timeline.filter((n) => !(n.kind === 'permission' && state._lines.has(n.request_id)))
  for (const [id, asked] of older._requests) {
    if (!state._requests.has(id)) state._requests.set(id, asked)
    const line = state._lines.get(id)
    if (line && line.input === null && line.header === null) Object.assign(line, lineAbout(asked))
  }
  for (const [id, line] of older._lines) if (!state._lines.has(id)) state._lines.set(id, line)
  for (const id of older._resolved) state._resolved.add(id)
  // A call in the older page whose result this page already read, as a step of
  // its own because the call was above the window, is one step and not two: the
  // result goes onto the call and the stand-in goes. And a call the newer part
  // never answered, below an exit this page has read, ended with that process.
  for (const [id, tool] of older._tools) {
    const later = state._tools.get(id)
    if (tool.result === null && later?.earlier) {
      tool.result = later.result
      tool.isError = later.isError
      tool.ended = false
      unplace(state, later)
      state._tools.set(id, tool)
    } else if ((tool.result === null || isPlan(tool)) && state._exited) {
      tool.ended = true
    }
  }
  if (older._exited) state._exited = true
  state.timeline.unshift(...lines)
  // The turn lines go in front too, and in order, which is the whole of what
  // pricing needs: the record's `turns` is the session's and this page's lines
  // are a suffix of it, wherever the page got them from.
  state._results.unshift(...older._results)
  // A compaction further up is only the latest one if this page has read none
  // below it.
  if (!state.compaction && older.compaction) state.compaction = older.compaction
  for (const [id, tool] of older._tools) if (!state._tools.has(id)) state._tools.set(id, tool)
  for (const [id, node] of older._messages) if (!state._messages.has(id)) state._messages.set(id, node)
  for (const [block, at] of older._placedTools) state._placedTools.set(block, at)
  for (const [id, questions] of older._asked) if (!state._asked.has(id)) state._asked.set(id, questions)
  return priceFromSnapshot(state)
}

function markSeen(state, seq) {
  state.seen.add(seq)
  if (state.lastSeq === null || seq > state.lastSeq) state.lastSeq = seq
}

// A permission answered by this page. `broker.permission_resolved` says the
// same thing to every other client watching; this is only so the card goes
// the instant the answer is accepted rather than a round trip later.
//
// A question answered here leaves its line in the thread at once, with what was
// chosen — `{ behavior, answers }`, the body that was sent — and the event that
// follows lands on the same line rather than drawing a second one.
export function resolvePermission(state, requestId, answer = null) {
  if (answer && state._asked.has(requestId)) noteAnswer(state, { request_id: requestId, ...answer })
  // The line says what was sent and not what was granted: "for this chat" is
  // the host's to say, on the resolved event, and a host that does not carry
  // the rule through says nothing of it.
  else if (answer) leaveLine(state, requestId, answer.behavior === 'allow' ? 'allow' : 'deny', { provisional: true })
  return forgetPermission(state, requestId)
}

// The answer route said `404`: the host holds no such request. Whatever closed
// it — the process ending under it, most often, on a host from before closing
// events — the card is answering nothing, so it goes, and the line it leaves is
// the likeliest reading until an event says otherwise.
export function dropPermission(state, requestId) {
  leaveLine(state, requestId, 'ended', { provisional: true })
  return forgetPermission(state, requestId)
}

// A message the person just sent. The CLI does not echo it, so the page draws
// it; the broker logs it as `broker.user_message` for every client including
// this one, and the returned node is how the two are kept to one line.
//
// What it carried goes on the line with it: the files are on the record too, so
// the same message replayed tomorrow draws the same chips.
export function noteUserMessage(state, text, attachments = []) {
  const node = { kind: 'user', text, attachments: attachmentList(attachments), seq: null, pending: true }
  state.timeline.push(node)
  return node
}

// The seq the messages route answered with is the seq of *this* message's
// `broker.user_message`. Claiming it here means the event, when it arrives,
// changes nothing.
export function labelUserMessage(state, node, seq) {
  if (!node || seq === undefined || seq === null || node.seq !== null) return state
  node.seq = seq
  node.pending = false
  markSeen(state, seq)
  return state
}

// Whether this page has read as far as the log went when the record was last
// sent. It is the one honest use of `record_seq`: not a position to reconnect
// at — it is the session's number, not this page's — but the answer to "is what
// I am drawing history or is it happening now", which is the difference between
// "catching up" and "live" on the status line. A record that names no number
// cannot say otherwise, so it does not.
export function caughtUp(state) {
  const recordSeq = state?.session?.record_seq
  if (typeof recordSeq !== 'number') return true
  return (state.lastSeq ?? 0) >= recordSeq
}

export function dismissNotice(state, id) {
  state.notices = state.notices.filter((n) => n.id !== id)
  return state
}

// ---------------------------------------------------------------- the broker

// Every `broker.` event is flat: the record's fields sit beside `type` and
// `seq`. `seq` is dropped rather than kept, so nothing downstream can read a
// number off the record and mistake it for an event's identity; the record's
// own position is `record_seq` and it is kept.
function fieldsOf(event) {
  const { type, seq, ...rest } = event
  return rest
}

// The two fields a windowed answer's snapshot carries, and they are read off it
// here rather than being merged into the record.
//
// **They say where *this answer* starts, not where the session is.** A thread
// opened at `?last=10` is told the seq of the first event it was sent and how
// many turns sit above it; the next page of history says the same about itself.
// Merged into `state.session` they would outlive the answer they describe — the
// live list's snapshots do not carry them, and a merge keeps what it is not
// told about — so the page would go on believing an old window's numbers. They
// live apart, and `record_seq` is still the only thing that says where the log
// ends.
function readWindow(state, event) {
  if (typeof event.window_first_seq === 'number') state.window.firstSeq = event.window_first_seq
  if (typeof event.turns_before === 'number') state.window.turnsBefore = event.turns_before
  return state
}

function brokerSession(state, event) {
  readWindow(state, event)
  const { window_first_seq: _first, turns_before: _before, ...rec } = fieldsOf(event)
  state.session = { ...(state.session ?? {}), ...rec }
  // A record written at or after the exit this page is holding, saying the
  // process is alive: the session has been resumed — from another device, or
  // by this one — and the exit is about the process before it. `record_seq` is
  // what makes the comparison honest: it is where the log had got to when the
  // record was written, and a record written before the exit was logged cannot
  // say anything about it.
  if (state.exit && rec.state === 'running' && (state.exit.seq === null || (typeof rec.record_seq === 'number' && rec.record_seq >= state.exit.seq))) {
    state.exit = null
  }
  // A session opened while it is mid-question shows its card from the record.
  // Anything already answered stays answered: `broker.permission_resolved` is
  // what takes a card away, and a record that has not caught up cannot undo it.
  //
  // **And the record's list is authoritative the other way too.** A card whose
  // request the record no longer holds is answering nothing — the process ended
  // under it, or it closed in a way this log never said — so it goes, if the
  // record was written at or after the card's request. A record older than the
  // request cannot speak for it. The list and the number it was true at are
  // kept, so a request replayed *after* this snapshot — the opening window, a
  // page of earlier history — is judged by the same rule on arrival.
  //
  // The host can report bare request IDs. Keep the logged card for each ID; only a full
  // request includes enough information to construct a card from the snapshot.
  if (Array.isArray(rec.pending_permissions)) {
    const ids = new Set(rec.pending_permissions.map(pendingId).filter(Boolean))
    const at = typeof rec.record_seq === 'number' ? rec.record_seq : null
    if (at !== null) {
      state._pending = { ids, at }
      for (const p of [...state.permissions]) {
        if (ids.has(p.request_id) || (p.seq !== null && p.seq > at)) continue
        leaveLine(state, p.request_id, 'ended', { provisional: true })
        forgetPermission(state, p.request_id)
      }
    }
    for (const p of rec.pending_permissions) addPermission(state, p)
  }
  return state
}

function pendingId(p) {
  return typeof p === 'string' ? p : (p?.request_id ?? null)
}

// What a request asked, as far as its line needs: the tool, the input its
// target is read from, and a question's header.
function lineAbout(asked) {
  if (!asked) return { tool_name: null, input: null, header: null }
  return { tool_name: asked.tool_name ?? null, input: asked.input ?? null, header: asked.header ?? null }
}

// **Every way a card goes leaves one line where it stood**, in the page's own
// words (`app.js` writes them from `behavior`): allowed, allowed for this
// chat, denied, not answered, withdrawn, ended with the chat. A question
// answered or declined has its own, fuller line (`noteAnswer`), and is not
// drawn twice.
//
// One line per request. The first reading may be provisional — this page's own
// answer, a `404`, a record that no longer lists it — and the host's event,
// when it comes, corrects it in place rather than adding a second.
function leaveLine(state, requestId, behavior, { seq = null, scope = null, provisional = false, about = null } = {}) {
  if (!requestId) return
  if (state.timeline.some((n) => n.kind === 'answer' && n.request_id === requestId)) return
  const existing = state._lines.get(requestId)
  if (existing) {
    if (provisional && !existing.provisional) return
    existing.behavior = behavior
    existing.scope = scope
    existing.provisional = provisional
    if (existing.seq === null && seq !== null) existing.seq = seq
    if (existing.input === null && existing.header === null) Object.assign(existing, lineAbout(about ?? state._requests.get(requestId)))
    return
  }
  const line = { kind: 'permission', request_id: requestId, behavior, scope, provisional, seq, ...lineAbout(about ?? state._requests.get(requestId)) }
  state._lines.set(requestId, line)
  state.timeline.push(line)
}

// Two kinds of asking, and the host says which: `kind: "approval"` is "may I
// run this", answered allow or deny; `kind: "question"` is a form the person
// fills — options to choose, a value to type — and it arrives in one shape
// whichever provider asked it, so nothing here reads a vendor's own format.
//
// **Anything that is not a well-formed question is an approval.** An event
// with no `kind` is from a broker older than questions; a `kind` this page has
// not met is the same; and a question with no questions in it, or one it could
// not key an answer by, is drawn as the approval card with its raw input — so a
// person can always at least say no, and nothing the host asks is undrawable.
function addPermission(state, raw, seq = null) {
  const p = raw && typeof raw === 'object' ? raw : {}
  if (!p.request_id) return
  const questions = p.kind === 'question' ? questionList(p.questions) : null
  if (!state._requests.has(p.request_id)) {
    state._requests.set(p.request_id, {
      tool_name: p.tool_name ?? null,
      input: questions ? null : (p.input ?? null),
      header: questions ? questions[0].header || questions[0].question || null : null,
    })
  }
  if (questions && !state._asked.has(p.request_id)) state._asked.set(p.request_id, questions)
  if (state._resolved.has(p.request_id)) return
  if (state.permissions.some((existing) => existing.request_id === p.request_id)) return
  // Asked at or before a record that no longer holds it: replayed history, and
  // already closed. Its line is drawn here, where it was asked; an event further
  // down that says how it closed corrects the line rather than adding one.
  const pending = state._pending
  if (seq !== null && pending && seq <= pending.at && !pending.ids.has(p.request_id)) {
    leaveLine(state, p.request_id, 'ended', { seq, provisional: true })
    state._resolved.add(p.request_id)
    return
  }
  if (questions) {
    state.permissions.push({
      kind: 'question',
      request_id: p.request_id,
      seq,
      tool_name: p.tool_name ?? null,
      provider: p.provider ?? null,
      // The provider's own sentence above the questions, when it sent one.
      message: typeof p.message === 'string' && p.message.trim() !== '' ? p.message : null,
      questions,
    })
    return
  }
  state.permissions.push({
    kind: 'approval',
    request_id: p.request_id,
    seq,
    tool_name: p.tool_name ?? 'a tool',
    // A malformed question has no `input` of its own; what it did carry is
    // the raw thing to show.
    input: p.input ?? (p.kind === 'question' && p.questions !== undefined ? { questions: p.questions } : {}),
    description: p.description ?? '',
    // The host's own words for why it is asking, and its category for them —
    // `workingDir` is the one the card says in its own words.
    decision_reason: p.decision_reason ?? '',
    decision_reason_type: typeof p.decision_reason_type === 'string' ? p.decision_reason_type : null,
    // What the CLI proposes an "allow for this chat" would add, sent back as
    // given — **its `session` entries only**. The CLI also proposes rules for
    // `localSettings` (for `Skill`, mostly), which would have it write the
    // persona's own settings file from a tap on a card; the host refuses any
    // body carrying one with a 400, by design, and a session rule is the only
    // thing "for this chat" means. So the rest are dropped here, once, and
    // every reader — whether the action is drawn, its sub-line, what is sent —
    // sees only what may leave the page.
    suggestions: Array.isArray(p.permission_suggestions) ? p.permission_suggestions.filter((x) => x && typeof x === 'object' && x.destination === 'session') : [],
  })
}

// The questions of a `kind: "question"` request, read strictly: a list of one or
// more, each with an `id` its answer is keyed by. Anything less is `null`, and
// the request is drawn as an approval. Labels are kept exactly as written —
// "(Recommended)" is the model's word and stays in the label, and the label is
// what goes back as the answer.
function questionList(raw) {
  if (!Array.isArray(raw) || raw.length === 0) return null
  const out = []
  for (const q of raw) {
    if (!q || typeof q !== 'object') return null
    if (typeof q.id !== 'string' || q.id === '') return null
    const options = (Array.isArray(q.options) ? q.options : [])
      .filter((o) => o && typeof o.label === 'string' && o.label !== '')
      .map((o) => ({
        label: o.label,
        description: typeof o.description === 'string' && o.description !== '' ? o.description : null,
        preview: typeof o.preview === 'string' && o.preview !== '' ? o.preview : null,
      }))
    out.push({
      id: q.id,
      header: typeof q.header === 'string' ? q.header : '',
      question: typeof q.question === 'string' ? q.question : '',
      options,
      multi: q.multi === true,
      // A question with no options is answered by typing or not at all, so its
      // field is there whatever `other` says.
      other: q.other === true || options.length === 0,
      secret: q.secret === true,
      required: q.required !== false,
    })
  }
  return out
}

// The line an answered question leaves in the thread: what was asked, when this
// page saw it asked, and what came back. One per request — the page that
// answered drew it the moment it sent, and the event lands on the same line.
function noteAnswer(state, a) {
  const questions = state._asked.get(a.request_id) ?? null
  const answers = a.behavior === 'allow' && a.answers && typeof a.answers === 'object' ? a.answers : null
  const existing = state.timeline.find((n) => n.kind === 'answer' && n.request_id === a.request_id)
  if (existing) {
    existing.behavior = a.behavior
    existing.answers = answers ?? existing.answers
    if (existing.seq === null && a.seq !== undefined) existing.seq = a.seq
    return
  }
  state.timeline.push({ kind: 'answer', request_id: a.request_id, behavior: a.behavior, answers, questions, seq: a.seq ?? null })
}

function permissionRequest(state, event) {
  addPermission(state, fieldsOf(event), typeof event.seq === 'number' ? event.seq : null)
  return state
}

// The five ways the host says a request closed. Anything else it might one day
// send reads as answered or not by what it says, never as an error.
const CLOSINGS = new Set(['allow', 'deny', 'expired', 'cancelled', 'ended'])

// An answer was accepted — by this page, or by the other device watching the
// same session. Either way the question is gone, and it must not come back
// when the record that still lists it is replayed.
//
// Answered, or not: `expired` (nobody answered in time and the model went on),
// `cancelled` (the CLI withdrew it) and `ended` (the process ended with it
// open) close a card exactly as an answer does, and each leaves its line.
function permissionResolved(state, event) {
  const behavior = CLOSINGS.has(event.behavior) ? event.behavior : 'ended'
  const answered = behavior === 'allow' || behavior === 'deny'
  if (event.request_id && answered && (event.kind === 'question' || state._asked.has(event.request_id))) {
    // A question replayed past a record that no longer held it drew a
    // provisional line where it was asked; its answer takes that place.
    const stale = state._lines.get(event.request_id)
    noteAnswer(state, event)
    if (stale) {
      state._lines.delete(event.request_id)
      const answer = state.timeline.findLast((n) => n.kind === 'answer' && n.request_id === event.request_id)
      state.timeline = state.timeline.filter((n) => n !== answer)
      state.timeline.splice(state.timeline.indexOf(stale), 1, answer)
    }
  } else {
    const about = state._requests.get(event.request_id) ?? { tool_name: event.tool_name ?? null, input: null, header: null }
    leaveLine(state, event.request_id, behavior, { seq: event.seq ?? null, scope: typeof event.scope === 'string' ? event.scope : null, about })
  }
  return forgetPermission(state, event.request_id)
}

function forgetPermission(state, requestId) {
  if (!requestId) return state
  state._resolved.add(requestId)
  state.permissions = state.permissions.filter((p) => p.request_id !== requestId)
  return state
}

function brokerUserMessage(state, event) {
  const text = typeof event.text === 'string' ? event.text : ''
  // What came with it. The record is the authority on this — a message reopened
  // tomorrow draws its files from here and from nothing this browser kept.
  const attachments = attachmentList(event.attachments)
  // This goes to every client, the sender included. If this page drew the line
  // when the person pressed send, that line is this event and not another one.
  if (claimSent(state, text, event.seq, attachments)) return state
  state.timeline.push({ kind: 'user', text, attachments, seq: event.seq ?? null, pending: false })
  return state
}

function claimSent(state, text, seq, attachments) {
  for (const node of state.timeline) {
    if (node.kind !== 'user' || !node.pending) continue
    if (node.seq !== null && node.seq !== seq) continue
    if (node.text !== text) continue
    node.pending = false
    node.seq = seq ?? node.seq
    // The record's list, over the one the page drew from its own uploads: the
    // names are the host's, and a file it renamed on the way down is the name
    // this line will have from now on anyway.
    if (attachments.length > 0) node.attachments = attachments
    return true
  }
  return false
}

function brokerExit(state, event) {
  // Nothing is asking once the process that asked has gone. A host that closes
  // each request before its exit has already taken these; one from before that
  // did not, and its log is still full of them.
  for (const p of [...state.permissions]) {
    leaveLine(state, p.request_id, 'ended', { seq: event.seq ?? null })
    forgetPermission(state, p.request_id)
  }
  endUnfinished(state)
  const code = event.exit_code ?? null
  state.exit = { code, seq: event.seq ?? null }
  state._exited = true
  if (state.session) state.session.state = 'exited'
  state.timeline.push({ kind: 'exit', code })
  return state
}

// **A step dies with its process.** A call with no result when its process
// ended is never going to get one from that process, and drawn as it was — a
// call still waiting — it kept a fold spinning beside a line saying its request
// had ended, for as long as the chat existed. So it is marked `ended`: neither
// running nor failed, read as "ended with the chat", the words its request's
// line already says. A result that does arrive for it later (a resumed CLI
// writing its own account of the interruption) is attached as any result is,
// and is what the row then says. A block of thinking being written when the
// process went is finished for the same reason.
//
// **A plan stops with its process too**, though its call has its result: a
// running plan's state is the plan's own — an item still `in_progress` — and the
// process that would have moved it on has gone. So a plan card is `ended` as
// well, and reads that way while it has an item in flight. A later plan update
// is a card of its own, from the process that is running now, and is the one
// that says where the plan is.
function endUnfinished(state) {
  for (const tool of state._tools.values()) if (tool.result === null || isPlan(tool)) tool.ended = true
  for (const node of state._messages.values()) {
    node.streaming = false
    for (const block of node.blocks) if (block?.type === 'thinking') block.streaming = false
  }
  state._open.clear()
}

// Take a stand-in step out of wherever it was drawn: the thread, or a
// subagent's conversation under the call that started it.
function unplace(state, block) {
  const lists = [state.timeline, ...[...state._tools.values()].map((t) => t.children)]
  for (const list of lists) {
    const at = list.findIndex((n) => n?.kind === 'tool' && n.block === block)
    if (at !== -1) {
      list.splice(at, 1)
      return
    }
  }
}

// The host replaced the older history with a summary: a line in the timeline at
// its own `seq`, and the latest one remembered, because until the next real
// turn states a reading the context *is* what the summary left.
//
// **This, and not the CLI's own `system`/`compact_boundary`, is the one that is
// drawn.** Both are in the log; the boundary passes through as a `system` event
// this reducer does not draw, and drawing both would be one compaction twice.
function brokerCompaction(state, event) {
  const num = (n) => (typeof n === 'number' && Number.isFinite(n) ? n : null)
  const node = {
    kind: 'compaction',
    seq: event.seq ?? null,
    trigger: event.trigger === 'manual' || event.trigger === 'auto' ? event.trigger : null,
    before: num(event.tokens_before),
    after: num(event.tokens_after),
    at: typeof event.at === 'string' ? event.at : null,
  }
  state.timeline.push(node)
  if (!state.compaction || node.seq === null || state.compaction.seq === null || node.seq > state.compaction.seq) state.compaction = node
  return state
}

// "Compacted · 312k → 2k", or "Compacted" when the host could not say how much.
// One sentence, so the divider in the thread and the notice row after Compact
// now cannot say it two ways.
export function compactionWords(node) {
  if (!node) return 'Compacted'
  const { before, after } = node
  if (typeof before === 'number' && typeof after === 'number') return `Compacted · ${tokens(before)} → ${tokens(after)}`
  return 'Compacted'
}

// What the session has running in the background, by name. The host sends this
// whenever the count changes, so a thread that is open follows it without
// asking for anything; the count on the record says the same number and is what
// a row on the home screen has.
//
// It is not a line in the timeline: a background task is a thing that is
// happening, not a thing that was said.
function backgroundTasks(state, event) {
  const tasks = Array.isArray(event.tasks) ? event.tasks : []
  state.tasks = tasks
    .filter((t) => t && typeof t === 'object')
    .map((t) => ({
      id: typeof t.task_id === 'string' ? t.task_id : null,
      type: typeof t.task_type === 'string' ? t.task_type : null,
      description: typeof t.description === 'string' ? t.description : '',
    }))
  return state
}

// Whether this page is waiting on the session for something.
//
// The record's `status` is the host's word for a turn in flight, waiting on
// background work or paused for a permission. A local
// pending send is not evidence of work. An exit clears a stale working record
// until a later event (or an explicit resume) clears that exit.
//
// **The settled statuses are the ones named** — `SETTLED_STATUSES`, which is the
// record's vocabulary and lives with the record's model — so a word the broker
// grows (`waiting` is the one being added) reads as a turn in flight rather than
// as a session with nothing happening. Wrong in that direction is a spinner that
// stays a moment too long; wrong in the other is a chat that says it is not
// running while it is.
export function inFlight(state, status = state?.session?.status) {
  if (state?.exit) return false
  if (typeof status === 'string' && status.trim() !== '' && !SETTLED_STATUSES.has(status.trim())) return true
  if (status === 'needs_input') return true
  return false
}

// Whether the session's process is alive, as far as this stream and this record
// can tell. `state` is the host's word for it; the exit event is what says so
// before the next snapshot arrives, and it is cleared the moment anything
// later than it does.
export function alive(state, processState = state?.session?.state) {
  return processState === 'running' && !state?.exit
}

// The host has started the process again, under the same id, because this page
// asked it to. The exit this page is holding is about the process before it,
// and there is nothing ambiguous about the ordering: the answer to the resume
// came after it.
export function resumed(state) {
  state.exit = null
  if (state.session) state.session.state = 'running'
  return state
}

// ------------------------------------------------------------------- the CLI

// A retry has one shape and one place where words are written about it: the
// CLI's own `system` event with `subtype: "api_retry"`. Nothing else is
// pattern-matched into a banner, and free text is never read to guess at one.
const RETRY_REASON = {
  authentication_failed: 'the login was not accepted',
  oauth_org_not_allowed: 'the login’s organisation is not allowed',
  account_on_hold: 'the account is on hold',
  verification_required: 'the account needs verification',
  billing_error: 'a billing problem',
  rate_limit: 'a rate limit',
  overloaded: 'the model is overloaded',
  invalid_request: 'the request was rejected as invalid',
  model_not_found: 'the model was not found',
  server_error: 'a server error',
  unknown: 'an error it could not name',
  max_output_tokens: 'the output token limit',
  cloud_credential_error: 'a cloud credential problem',
}

// `error` is a category, not a sentence. This is the one place in the app that
// turns it into one, from the category, the HTTP status and the wait.
export function retrySentence(event) {
  const reason = RETRY_REASON[event.error] ?? 'an error'
  const status = typeof event.error_status === 'number' ? ` (HTTP ${event.error_status})` : ''
  const delay = typeof event.retry_delay_ms === 'number' ? ` in ${round(event.retry_delay_ms / 1000)}s` : ''
  const attempt =
    typeof event.attempt === 'number'
      ? ` — attempt ${event.attempt}${typeof event.max_retries === 'number' ? ` of ${event.max_retries}` : ''}`
      : ''
  return `Retrying${delay} after ${reason}${status}${attempt}.`
}

function round(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

function system(state, event) {
  if (event.subtype === 'init') {
    state.init = {
      model: event.model ?? null,
      workspace: event.cwd ?? null,
      tools: Array.isArray(event.tools) ? event.tools : [],
    }
    return state
  }

  if (event.subtype === 'api_retry') {
    // One banner per run of retries, replaced as the attempts climb, rather
    // than ten banners stacked up saying the same thing.
    state.notices = state.notices.filter((n) => n.kind !== 'retry')
    state.notices.push({ id: `retry:${event.seq ?? state.notices.length}`, kind: 'retry', text: retrySentence(event) })
    return state
  }

  // `status`, `thinking_tokens`, and whatever the CLI adds next. Not this
  // page's vocabulary, so not this page's line.
  return state
}

// Where a message belongs. An event from a background subagent carries the id
// of the tool call that spawned it, and goes inside that call rather than
// between the lines of the main reply.
function containerFor(state, event) {
  const parent = event.parent_tool_use_id
  if (parent) {
    const tool = state._tools.get(parent)
    if (tool) return tool.children
  }
  return state.timeline
}

function assistantNode(state, event, messageId, model) {
  const key = messageId ?? `anon:${event.seq}`
  let node = state._messages.get(key)
  if (!node) {
    node = {
      kind: 'assistant',
      id: key,
      parent: event.parent_tool_use_id ?? null,
      model: model ?? null,
      blocks: [],
      // How many of this message's blocks the CLI has stated outright, as
      // against streamed. The CLI states them a few at a time, under one
      // message id, so this is where the next `assistant` event writes.
      told: 0,
      streaming: true,
    }
    state._messages.set(key, node)
    containerFor(state, event).push(node)
  }
  if (model && !node.model) node.model = model
  return node
}

function textBlock(node, index) {
  let block = node.blocks[index]
  if (!block || block.type !== 'text') {
    block = { type: 'text', text: '' }
    node.blocks[index] = block
  }
  return block
}

// A tool block is shared: the streamed copy and the finished message's copy are
// one object, so the result that comes back later lands in both. Shared means
// it can be reached from two places, and two places is two renderings — so
// putting it somewhere takes it out of wherever it was. One call, one line.
function placeTool(state, node, index, block) {
  const previous = state._placedTools.get(block)
  if (previous && (previous.node !== node || previous.index !== index)) {
    if (previous.node.blocks[previous.index] === block) previous.node.blocks[previous.index] = null
  }
  state._placedTools.set(block, { node, index })
  node.blocks[index] = block
}

// `stream_event` is the reply as it is written. The whole message arrives
// again afterwards as an `assistant` event, which is the authority; this is
// what makes the wait bearable, not what makes the transcript true.
function streamEvent(state, event) {
  const e = event.event
  if (!e || typeof e !== 'object') return state

  const parentKey = event.parent_tool_use_id ?? ''

  if (e.type === 'message_start') {
    const node = assistantNode(state, event, e.message?.id ?? null, e.message?.model)
    state._open.set(parentKey, node)
    return state
  }

  // Every other streaming event belongs to the message being written, and only
  // `message_start` says which that is. A reconnect lands mid-message and the
  // partials for its opening are gone — so a partial with no open message is
  // dropped rather than invented into a message of its own. The `assistant`
  // event is the authority and it is still coming.
  const node = state._open.get(parentKey)
  if (!node) return state
  const index = e.index ?? 0

  if (e.type === 'content_block_start') {
    const block = e.content_block ?? {}
    if (block.type === 'tool_use') {
      placeTool(state, node, index, newTool(state, block.id, block.name, block.input))
    } else if (block.type === 'thinking') {
      // **A thinking block being written is a step that is still going.** It is
      // what a fold reads out while a turn is working, before there is any call
      // to wait on — so whether it has finished is a fact about the block, said
      // where the stream is read, and not something a page works out from the
      // session's status.
      node.blocks[index] = { type: 'thinking', text: block.thinking ?? '', streaming: true }
    } else {
      node.blocks[index] = { type: 'text', text: block.text ?? '' }
    }
    return state
  }

  if (e.type === 'content_block_delta') {
    const delta = e.delta ?? {}
    if (delta.type === 'text_delta') {
      textBlock(node, index).text += delta.text ?? ''
    } else if (delta.type === 'thinking_delta') {
      const block = node.blocks[index] ?? (node.blocks[index] = { type: 'thinking', text: '', streaming: true })
      block.text = (block.text ?? '') + (delta.thinking ?? '')
    } else if (delta.type === 'input_json_delta') {
      const block = node.blocks[index]
      if (block && block.type === 'tool') block.partialInput = (block.partialInput ?? '') + (delta.partial_json ?? '')
    }
    return state
  }

  if (e.type === 'content_block_stop') {
    const block = node.blocks[index]
    if (block && block.type === 'thinking') block.streaming = false
    if (block && block.type === 'tool' && block.partialInput) {
      try {
        block.input = JSON.parse(block.partialInput)
      } catch {
        // Half a JSON object is not an argument list; leave what was shown.
      }
    }
    return state
  }

  if (e.type === 'message_stop') {
    node.streaming = false
    state._open.delete(parentKey)
  }
  return state
}

function newTool(state, id, name, input) {
  const existing = id ? state._tools.get(id) : null
  if (existing) return existing
  const block = {
    type: 'tool',
    id: id ?? null,
    name: name ?? 'tool',
    input: input && typeof input === 'object' && Object.keys(input).length > 0 ? input : {},
    partialInput: '',
    result: null,
    isError: false,
    // The process ended with this call still waiting on its result — see
    // `endUnfinished()`. Not an error: the call did not fail, it was cut off.
    ended: false,
    // Whether this is a result whose call is above the window this page opened
    // at — a step the page knows the outcome of and not the asking. See `user()`.
    earlier: false,
    // A subagent's whole conversation, under the call that started it.
    children: [],
  }
  if (id) state._tools.set(id, block)
  return block
}

// The message, complete — and **a message arrives in as many of these as it
// has content blocks**. Recorded from a real session: a reply that says a
// sentence and then calls a tool is two `assistant` events under one message
// id, the first carrying `[text]` and the second `[tool_use]`, and the first
// of them arrives *before* the tool block has even started streaming.
//
// So an event's content is the next few blocks of the message and never the
// whole of it. It is written at the point the message has been told about so
// far and the blocks before it are left alone. Replacing them is what made a
// sentence vanish the moment the tool call it introduced arrived — the text
// was the message's whole content until the second event said the message's
// content was a tool call.
//
// The index it writes at is the block's own index in the message, which is the
// same index the stream used, so each block lands on the streamed copy of
// itself: a tool call keeps the one place it is drawn and a sentence is
// replaced by the authoritative copy of the same sentence.
function assistant(state, event) {
  const message = event.message ?? {}
  const node = assistantNode(state, event, message.id ?? null, message.model)
  node.parent = event.parent_tool_use_id ?? null
  node.model = message.model ?? node.model
  node.usage = message.usage ?? node.usage
  // Whether the message is still being written is the stream's to say, not
  // this event's: these arrive mid-message. A message nothing is streaming —
  // a replay from the log, or one whose `message_stop` has been — is finished.
  if (state._open.get(event.parent_tool_use_id ?? '') !== node) node.streaming = false

  const content = Array.isArray(message.content) ? message.content : []
  let at = node.told ?? 0
  for (const item of content) {
    if (item?.type === 'text') {
      node.blocks[at] = { type: 'text', text: item.text ?? '' }
    } else if (item?.type === 'thinking') {
      // **The streamed block is written into rather than replaced.** A thinking
      // block is a step inside a fold now, and the page keys a fold off the
      // identity of its first step — so a new object here would rebuild the card
      // and lose whether the reader had it open. The same reason `placeTool()`
      // keeps one object for a call.
      const already = node.blocks[at]
      if (already && already.type === 'thinking') {
        already.text = item.thinking ?? ''
        already.streaming = false
      } else {
        node.blocks[at] = { type: 'thinking', text: item.thinking ?? '' }
      }
    } else if (item?.type === 'tool_use') {
      const block = newTool(state, item.id, item.name, item.input)
      block.name = item.name ?? block.name
      if (item.input && typeof item.input === 'object') block.input = item.input
      placeTool(state, node, at, block)
    }
    // A block type this page has not met still spends its place in the
    // message, so everything after it stays where the stream put it.
    at += 1
  }
  node.told = at
  return state
}

// Provider user events carry tool results. A person's input has its own broker event.
// A history page can start after the call, so retain an unmatched result as a step.
function user(state, event) {
  const content = event.message?.content
  if (!Array.isArray(content)) return state

  for (const item of content) {
    if (item?.type !== 'tool_result') continue
    const tool = item.tool_use_id ? state._tools.get(item.tool_use_id) : null
    if (tool) {
      tool.result = flatten(item.content)
      tool.isError = Boolean(item.is_error)
      tool.ended = false
      continue
    }
    const step = newTool(state, item.tool_use_id ?? null, null, null)
    step.earlier = true
    step.result = flatten(item.content)
    step.isError = Boolean(item.is_error)
    containerFor(state, event).push({ kind: 'tool', block: step })
  }
  return state
}

function flatten(content) {
  if (content === null || content === undefined) return ''
  if (typeof content === 'string') return content
  // Images, including the broker's omitted_chars stubs, are media rather than
  // tool JSON. Never put an encoded image or its transport metadata in a fold.
  if (content.type === 'image') return '[image]'
  if (content.type === 'text') return content.text ?? ''
  if (Array.isArray(content)) {
    return content.map(flatten).join('\n')
  }
  return JSON.stringify(content)
}

// The end of a turn, and the line under it: what it cost, and which model.
// Every number here is the broker's or the CLI's. This app computes none — and
// in particular it never subtracts one running total from another, because
// only the host knows where each process began.
//
// The cost and the model are not on this event at all. They arrive on the
// snapshot the broker sends when it writes the turn record, a moment later, and
// until then they are unknown — which the page shows as nothing.
function result(state, event) {
  const usage = event.usage ?? {}
  const node = {
    kind: 'result',
    seq: event.seq ?? null,
    subtype: event.subtype ?? null,
    isError: Boolean(event.is_error),
    text: typeof event.result === 'string' ? event.result : null,
    // What this turn cost: the record's `cost_delta_usd` once it arrives.
    cost: null,
    // The whole process's running total, which resets at a resume. Only ever
    // shown labelled as a total.
    totalCost: typeof event.total_cost_usd === 'number' ? event.total_cost_usd : null,
    durationMs: event.duration_ms ?? null,
    numTurns: event.num_turns ?? null,
    // The record's, and only the record's.
    model: null,
    // What the effort was set to for this turn, if the host records one. It is
    // not a field this interface documents on a turn, so it is read where it
    // would be and drawn only when it is there.
    effort: null,
    // How much of the model's context this turn's last request held, and how
    // much that model has. It is on the `result` that closes the turn and on
    // the turn record; either number may be `null`, which is unknown and is
    // shown as nothing.
    context: contextOf(event.context),
    usage: {
      input: usage.input_tokens ?? null,
      output: usage.output_tokens ?? null,
      cacheRead: usage.cache_read_input_tokens ?? null,
      cacheWrite: usage.cache_creation_input_tokens ?? null,
    },
    permissionDenials: Array.isArray(event.permission_denials) ? event.permission_denials : [],
  }
  containerFor(state, event).push(node)
  // One turn record per turn, and a turn is the session's own. A subagent's
  // `result` is not a turn, so it is drawn and never priced.
  if (!event.parent_tool_use_id) {
    const previous = state.timeline.slice(0, -1).findLast((n) => n.kind === 'user' || state._results.includes(n))
    const notice = state._writeFailure ?? (previous?.kind === 'user' ? previous.saveNotice : null)
    state._results.push(node)
    if (notice) {
      node.saveNotice = notice
      node.saveNotice.tone = 'bad'
      placeSaveNotice(state, node.saveNotice)
      state._writeFailure = null
    }
  }
  // The turn is over, so any retry it was waiting on is over too.
  state.notices = state.notices.filter((n) => n.kind !== 'retry')
  return state
}

// What a turn cost, and on what model, from the record and from nowhere else.
//
// The record's `turns` is the whole session's, across resumes. This page's turn
// lines are whatever it has watched, which is always a *suffix* of them: a page
// joins a session at some point and sees everything from there on. So the two
// need lining up at one end only, and `record_seq` says where that end is — it
// is the last event the record has counted, so once this page has read at least
// that far, the turn lines it drew up to that point are exactly the record's
// last few. Nothing is matched by its numbers, nothing is subtracted, and a
// turn line the record has not reached keeps an unknown cost until a snapshot
// arrives that has.
function priceFromSnapshot(state) {
  const turns = state.session && Array.isArray(state.session.turns) ? state.session.turns : null
  if (!turns) return state

  const recordSeq = state.session.record_seq
  if (typeof recordSeq !== 'number') return state
  // Mid-replay this page has not yet been told about every turn the record
  // counts, so the two ends do not line up yet. The next snapshot, or the rest
  // of the replay, is what makes them.
  if ((state.lastSeq ?? 0) < recordSeq) return state

  // A failed save can lose the result itself. Include the unanswered person's
  // turn as a slot, so its durable warning labels that gap and later turns do
  // not lend their cost/model to an earlier result. Ordinary brokers keep the
  // existing result-only correspondence.
  if (turns.some((turn) => saveReason(turn?.not_saved))) {
    // Even the first write can fail: an empty log has no user/result anchor.
    let slots = recordSeq === 0
      ? emptySaveSlots(state, turns)
      : saveSlots(state, recordSeq)
    // Only init/housekeeping may have survived; the latest failed turn still
    // needs a place even when its person's message was lost too.
    if (slots.length === 0 && saveReason(turns.at(-1)?.not_saved)) slots = emptySaveSlots(state, turns).slice(-1)
    const offset = turns.length - slots.length
    if (offset < 0) return state
    for (const [i, slot] of slots.entries()) {
      const turn = turns[offset + i]
      if (slot.anchor.kind === 'result' && turn && typeof turn === 'object') price(slot.anchor, turn)
      const reason = saveReason(turn?.not_saved)
      if (!reason) continue
      const notice = slot.anchor.saveNotice ??= saveNotice(reason)
      notice.text = saveSentence(reason)
      notice.tone = 'bad'
      placeSaveNotice(state, notice, slot.before)
      if (state._writeFailure === notice) state._writeFailure = null
      if (state._unrecordedSaveNotice === notice) state._unrecordedSaveNotice = null
    }
    return state
  }
  if (state._results.length === 0) return state

  const counted = state._results.filter((node) => node.seq !== null && node.seq <= recordSeq).length
  const offset = turns.length - counted
  // The record counts fewer turns than this page has watched: it is not the
  // record for this stream. Price nothing rather than price it wrongly.
  if (offset < 0) return state

  for (let i = 0; i < state._results.length; i += 1) {
    const turn = turns[offset + i]
    if (turn && typeof turn === 'object') price(state._results[i], turn)
  }
  return state
}

function saveReason(reason) {
  return typeof reason === 'string' && reason.trim() ? reason.trim() : null
}

function saveSentence(reason) {
  return `${reason}${/[.!?]$/.test(reason) ? '' : '.'} This reply may not be there after a reload.`
}

function saveNotice(reason) {
  return { kind: 'save_notice', text: saveSentence(reason), tone: 'quiet' }
}

function writeFailed(state, event) {
  const reason = saveReason(event.reason)
  if (!reason) return state
  if (state._writeFailures.has(event.from_seq)) return state
  // Once per turn; duplicates must neither multiply the line nor move a cursor.
  state._writeFailure ??= saveNotice(reason)
  state._unrecordedSaveNotice = state._writeFailure
  state._writeFailures.set(event.from_seq, state._writeFailure)
  const user = state.timeline.findLast((item) => item.kind === 'user')
  if (user) user.saveNotice = state._writeFailure
  placeSaveNotice(state, state._writeFailure)
  return state
}

function placeSaveNotice(state, notice, before = null) {
  const old = state.timeline.indexOf(notice)
  if (old !== -1) state.timeline.splice(old, 1)
  const at = before ? state.timeline.indexOf(before) : -1
  state.timeline.splice(at === -1 ? state.timeline.length : at, 0, notice)
}

function emptySaveSlots(state, turns) {
  return turns.map((_, i) => {
    const anchor = state._emptySaveAnchors[i] ??= {
      kind: 'missing',
      saveNotice: i === turns.length - 1 ? state._unrecordedSaveNotice : null,
      end: state.timeline.findLast((n) => n.kind !== 'save_notice') ?? null,
    }
    const after = anchor.end ? state.timeline.indexOf(anchor.end) + 1 : 0
    return { anchor, before: state.timeline.slice(after).find((n) => n.kind !== 'save_notice') }
  })
}

// Results and unanswered user messages, in conversation order. A following
// message/exit bounds a missing result; the current turn is only complete when
// the record says so. This also works for a window or a prepended history page.
function saveSlots(state, recordSeq) {
  const slots = []
  let pending = null
  const finish = (before) => {
    if (pending) slots.push({ anchor: pending, before })
    pending = null
  }
  for (const item of state.timeline) {
    if (item.kind === 'user') {
      finish(item)
      if (!item.pending && typeof item.seq === 'number' && item.seq <= recordSeq) pending = item
    } else if (item.kind === 'result' && state._results.includes(item) && typeof item.seq === 'number' && item.seq <= recordSeq) {
      const at = state.timeline.indexOf(item)
      const before = state.timeline.slice(at + 1).find((n) => n.kind !== 'save_notice') ?? null
      slots.push({ anchor: item, before })
      pending = null
    } else if (item.kind === 'exit') finish(item)
  }
  if (['done', 'failed', 'idle'].includes(state.session.status)) finish(null)
  return slots
}

function price(node, turn) {
  // Absent and `null` read the same way: unknown, which is shown as nothing and
  // never as zero. Turns recorded before the host wrote this key cannot be
  // backfilled, and a `null` is the host saying it could not compute one.
  node.cost = typeof turn.cost_delta_usd === 'number' ? turn.cost_delta_usd : null
  // Every turn record has a `model` key. `null` is the host saying nothing in
  // that turn named a model — an answer, shown the same way a missing number is.
  node.model = turn.model ?? null
  if (typeof turn.total_cost_usd === 'number') node.totalCost = turn.total_cost_usd
  // The record's reading of the same two facts. The `result` event carries the
  // context too, so a turn that has only just ended already has it; this is the
  // record saying the same thing, and a record that says nothing leaves what
  // the event said alone.
  const context = contextOf(turn.context)
  if (context) node.context = context
  // The turn a compaction leaves is the summary's, not a model answering: it is
  // priced like any other and it names no model under it.
  node.compaction = compactionTurn(turn)
  if (node.compaction) node.model = null
  if (typeof turn.effort === 'string' && turn.effort !== '') node.effort = turn.effort
}

// A compaction marks both context fields null. Older records omit the context object altogether.
export function compactionTurn(turn) {
  const c = turn?.context
  return Boolean(c && typeof c === 'object' && c.used === null && c.window === null)
}

function contextOf(raw) {
  if (!raw || typeof raw !== 'object') return null
  const used = typeof raw.used === 'number' ? raw.used : null
  const window = typeof raw.window === 'number' ? raw.window : null
  return used === null && window === null ? null : { used, window }
}

// "35k of 1M (3.5%)" — the one place this app puts a context reading into
// words, so the line under a turn and the line in the chat's own sheet cannot
// say it differently. Either number may be unknown: with no window there is no
// share to state, and with no reading there is nothing to state at all.
export function contextWords(raw) {
  const context = contextOf(raw)
  if (!context || context.used === null) return null
  const used = tokens(context.used)
  if (context.window === null || context.window <= 0) return used
  const share = (context.used / context.window) * 100
  return `${used} of ${tokens(context.window)} (${share < 0.1 ? '<0.1' : share.toFixed(1)}%)`
}

// Scale the fill against the model window. The warning setting controls colour only: amber
// at three quarters of the warning point, red at the point. Prefer the reported window,
// then the provider default. A numeric warning point can supply a scale when both are unknown.
export const CONTEXT_WARN = 0.75

// What the context holds now: the latest turn's reading, walking back past
// turns that stated none — **except past a compaction**, because the reading
// before it is history the summary has replaced. From a compaction until the
// next real turn the context is the compaction's `tokens_after`, when this page
// has read it (`compaction`, the transcript's latest), over the window the turn
// before it stated; and unknown — `null`, nothing drawn — when it has not.
export function contextNow(record, compaction = null) {
  const turns = Array.isArray(record?.turns) ? record.turns : []
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (compactionTurn(turns[i])) {
      const after = typeof compaction?.after === 'number' ? compaction.after : null
      if (after === null) return null
      let window = null
      for (let j = i - 1; j >= 0 && window === null; j -= 1) window = contextOf(turns[j]?.context)?.window ?? null
      return { used: after, window }
    }
    const c = contextOf(turns[i]?.context)
    if (c && c.used !== null) return c
  }
  return null
}

export const CONTEXT_WARN_AT = 400000

export function contextFill(record, compaction = null, warnAt = CONTEXT_WARN_AT, fallbackWindow = null) {
  const context = contextNow(record, compaction)
  if (!context) return null
  const positive = (n) => (typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null)
  const window = positive(context.window) ?? positive(fallbackWindow)
  const asked = warnAt === 'window' ? window : (positive(warnAt) ?? CONTEXT_WARN_AT)
  if (asked === null) return null
  const at = window === null ? asked : Math.min(asked, window)
  const scale = window ?? at
  const share = Math.min(1, Math.max(0, context.used / scale))
  const level = context.used >= at ? 'high' : context.used >= at * CONTEXT_WARN ? 'warn' : 'calm'
  const used = tokens(context.used)
  // "312k of 1M · warning at 400k" — the tap line, the notice and the sheet
  // all say this, so they cannot disagree.
  const words = `${window === null ? used : `${used} of ${tokens(window)}`} · warning at ${tokens(at)}`
  return {
    used: context.used,
    at,
    window,
    share,
    width: `${Math.round(share * 1000) / 10}%`,
    level,
    usedWords: used,
    words,
  }
}


// What to call a model on the page: the vendor's word and its version, out of
// the id the CLI states. `claude-opus-5` reads "Opus 5", `claude-fable-5-1`
// reads "Fable 5.1", and a dated id loses its date — eight digits are a release
// and not a version.
//
// It is in the seam because a model id's shape is the agent's dialect, like
// everything else in this file: a second agent naming its models some other way
// changes this function and no screen. An id this rule does not fit is shown as
// it is, which is always better than a name invented for it.
const VENDORS = new Set(['claude', 'anthropic', 'openai', 'google', 'xai'])

export function modelShortName(id) {
  if (typeof id !== 'string' || id.trim() === '') return null
  const parts = id.trim().split(/[-_]/).filter(Boolean)
  while (parts.length > 1 && VENDORS.has(parts[0].toLowerCase())) parts.shift()
  const name = parts.shift()
  if (!name) return id
  const version = parts.filter((p) => /^\d+$/.test(p) && p.length < 8)
  const word = name[0].toUpperCase() + name.slice(1)
  return version.length === 0 ? word : `${word} ${version.join('.')}`
}

// What a tool call *is*, for the three treatments the design draws: a plan
// card, a diff row, or the monospace command row everything else gets.
//
// This is the seam's job and not the page's, because every rule in it is a fact
// about one agent's dialect — that Claude Code keeps a running plan in a tool
// called `TodoWrite`, that a proposed one arrives as `ExitPlanMode`, that an
// edit names its file in `file_path`. A second agent names all three something
// else, and changes this file rather than the page.
//
// A change is only drawn as a diff once it has actually happened: an edit that
// is still waiting on a permission, or that came back an error, is a command
// row, because a diff row says a file now reads differently and it would not be
// true yet.
// What a call reads as when its process ended before it had a result: the words
// its request's line says, so a fold holding both says one thing twice rather
// than two things.
const ENDED = 'ended with the chat'

// A running plan: Claude Code's `TodoWrite`. See `endUnfinished()`.
function isPlan(tool) {
  return tool?.name === 'TodoWrite'
}

export function blockView(block) {
  const input = block.input ?? {}
  const done = block.result !== null && !block.isError

  if (block.name === 'TodoWrite' && Array.isArray(input.todos)) {
    const steps = input.todos.map((t) => ({
      text: typeof t?.content === 'string' ? t.content : String(t?.content ?? ''),
      state: t?.status === 'completed' ? 'done' : t?.status === 'in_progress' ? 'doing' : 'todo',
    }))
    const doing = input.todos.find((t) => t?.status === 'in_progress')
    const finished = steps.filter((s) => s.state === 'done').length
    // A step in flight when the process ended is not being done by anyone: the
    // card says what the plan had reached and that the chat ended under it, and
    // its title is not the present tense of a step nobody is doing.
    const cut = Boolean(doing) && block.ended
    return {
      view: 'plan',
      title: doing?.activeForm && !cut ? String(doing.activeForm) : `Planned ${steps.length} ${steps.length === 1 ? 'step' : 'steps'}`,
      sub: cut ? `${finished} of ${steps.length} done · ${ENDED}` : `${finished} of ${steps.length} done`,
      steps,
      running: Boolean(doing) && !cut,
    }
  }

  if (block.name === 'ExitPlanMode' && typeof input.plan === 'string') {
    const steps = planSteps(input.plan)
    return {
      view: 'plan',
      title: 'Plan',
      sub: block.ended ? ENDED : steps.length === 0 ? 'waiting on you' : `${steps.length} ${steps.length === 1 ? 'step' : 'steps'}`,
      steps,
      running: block.result === null && !block.ended,
    }
  }

  const edit = EDITS[block.name]
  if (edit && done && typeof input.file_path === 'string') {
    const counts = edit(input)
    if (counts) return { view: 'diff', file: input.file_path, added: counts.added, removed: counts.removed }
  }

  return {
    view: 'tool',
    // Bash is the one whose argument really is a command line; everything else
    // reads as its own name and the one argument worth seeing. A result whose
    // call is above the window has neither: what is known about it is that it is
    // a step from further up the conversation, and its output.
    command: block.earlier
      ? 'earlier step'
      : block.name === 'Bash' && typeof input.command === 'string'
        ? input.command
        : `${block.name} ${toolSummary(block)}`.trim(),
    sub: block.isError ? 'failed' : block.result !== null ? outputSize(block.result) : block.ended ? ENDED : 'running…',
    running: block.result === null && !block.ended,
    isError: block.isError,
  }
}

// Every tool this agent has that rewrites a file, and how to count what it
// changed. The numbers are the input's own lines; nothing is guessed from prose.
const EDITS = {
  Edit: (input) =>
    typeof input.new_string === 'string'
      ? { added: lineCount(input.new_string), removed: lineCount(input.old_string) }
      : null,
  Write: (input) => (typeof input.content === 'string' ? { added: lineCount(input.content), removed: 0 } : null),
  MultiEdit: (input) => {
    if (!Array.isArray(input.edits)) return null
    let added = 0
    let removed = 0
    for (const e of input.edits) {
      added += lineCount(e?.new_string)
      removed += lineCount(e?.old_string)
    }
    return { added, removed }
  },
}

function lineCount(text) {
  if (typeof text !== 'string' || text === '') return 0
  return text.split('\n').length
}

function outputSize(result) {
  const n = lineCount(result)
  return n === 0 ? 'no output' : `${n} ${n === 1 ? 'line' : 'lines'}`
}

// A plan is markdown. Its steps are its list items, and a plan with no list is
// a plan of one step: itself.
function planSteps(plan) {
  const items = plan
    .split('\n')
    .map((line) => /^\s*(?:[-*+]|\d+[.)])\s+(.*)$/.exec(line)?.[1]?.trim())
    .filter((text) => text)
  if (items.length > 0) return items.map((text) => ({ text, state: 'todo' }))
  const words = plan.trim()
  return words === '' ? [] : [{ text: words, state: 'todo' }]
}

// Group consecutive work steps, even a single step. The closed header reports failures,
// running calls and the latest reasoning so progress stays visible. A closed tool approval
// belongs with its call; a reply to a question stays in the transcript.
export function foldsAsStep(node) {
  return node?.kind === 'permission' && node.header === null
}

export function foldFacts(steps) {
  const run = Array.isArray(steps) ? steps : []
  let count = 0
  let failed = 0
  let running = false
  let thinking = null
  for (const step of run) {
    if (!step || typeof step !== 'object') continue
    count += 1
    // The line a closed approval left is a row of the fold and nothing more: it
    // is never running and never failed — a denial is an answer, not an error.
    if (step.kind === 'permission') continue
    if (step.type === 'thinking') {
      const line = lastLine(step.text)
      if (line) thinking = line
      // A block still being written is work in progress, exactly as a call with
      // no result is: it is what makes a fold read its live line before the turn
      // has anything to wait on.
      if (step.streaming) running = true
      continue
    }
    if (step.isError) failed += 1
    // A call its process ended under is finished, not failed: see `endUnfinished()`.
    if (step.result === null && !step.ended) running = true
  }
  return { count, failed, running, thinking }
}

// The latest thing the agent said to itself, on one line: the last line of the
// most recent block of thinking. The *end* of the paragraph and not its
// beginning — somebody watching a turn wants to know where it has got to.
function lastLine(text) {
  if (typeof text !== 'string') return null
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
  return lines.length === 0 ? null : lines[lines.length - 1]
}

// What a tool call reads as on its one line. The page asks for this rather
// than reaching into an agent's argument names itself.
export function toolSummary(block) {
  const input = block.input ?? {}
  if (['Agent', 'Task'].includes(block.name)) {
    const name = [input.name, input.description, input.subagent_type].find((value) => typeof value === 'string' && value.trim())
    if (name) return name.trim()
  }
  for (const key of ['command', 'file_path', 'path', 'pattern', 'url', 'query', 'prompt', 'description']) {
    const value = input[key]
    if (typeof value === 'string' && value.trim() !== '') return value.trim()
  }
  const keys = Object.keys(input)
  if (keys.length === 0) return block.partialInput ? '…' : ''
  return JSON.stringify(input)
}
