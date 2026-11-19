// A broker that is only a recording.
//
// It speaks the routes in this project's session interface and replays a
// recorded event stream with the pauses left in, so the whole app can be
// brought up and watched with no agent, no coding CLI login and no host
// anywhere near it. It is a development tool and a way to see the page work;
// it is never what the deployed app talks to.
//
//   node tools/fake-broker.js            # listens on 127.0.0.1:8932
//   BROKER_URL=http://127.0.0.1:8932 BROKER_TOKEN=dev npm start
//
// The token it wants is BROKER_TOKEN, or "dev".
//
// Everything it says about a session is a field the real interface has: a
// `status` the page reads rather than infers, a `title` the host writes after
// the first turn, the last thing the agent said, when it last showed a sign of
// life, and whether it is pinned or archived. The page must be able to draw a
// screen from those and nothing else, and this is where that is kept honest.

import http from 'node:http'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { activityFixture } from './activity-fixture.js'
import { createFakeSchedules } from './fake-schedules.js'
import { scheduleFilesFixture } from './schedule-files-fixture.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const TOKEN = process.env.BROKER_TOKEN ?? 'dev'
const PORT = Number(process.env.PORT ?? 8932)
const CAP = 2
const PING_MS = 15000
const MAX_ATTACHMENT = 10 * 1024 * 1024

function recorded(name) {
  return readFileSync(path.join(HERE, '..', 'test', 'fixtures', name), 'utf8')
    .split('\n')
    .filter((l) => l.trim() !== '')
    .map((l) => JSON.parse(l))
}

// Two recorded turns, one after the other, because they show different things.
// The first has a background subagent and a permission to answer. The second is
// a real session's stream, recorded from a coding CLI and sanitised: a
// sentence, a call, its result, a sentence, a call, its result, a sentence,
// then **five calls in a row** and a closing sentence — which is the shape that
// makes the thread's fold visible without a host, and the shape that showed the
// page was dropping every sentence an agent said before a tool call.
const TURNS = [recorded('session-with-subagent.jsonl'), recorded('turn-with-a-run-of-tools.jsonl')]
const RECORDING = TURNS.flat()
const RUN_TURN = TURNS[0].length // where the second recording begins

// The providers, wired or not. The three that are not are here because the page
// has a row for each of them, and "Codex — not wired" is an answer where a
// missing row is a bug report. They carry no `usage` key at all.
const PROVIDERS = [
  {
    id: 'claude-code',
    name: 'Claude Code',
    sign_in: { kind: 'paste-code', start: '/v1/providers/claude-code/login', status: '/v1/providers/claude-code/login', cancel: '/v1/providers/claude-code/login', code: '/v1/providers/claude-code/login/code', sign_out: null },
    connected: true,
    reason: null,
    // What a chat on this provider can be set to. The page offers exactly these
    // and invents none of its own: a model list is a fact about a vendor
    // account and changes without this app. The three that are not wired carry
    // no lists at all, so their rows offer nothing and say so.
    models: [
      { id: 'opus', name: 'Opus' },
      { id: 'sonnet', name: 'Sonnet' },
      { id: 'haiku', name: 'Haiku' },
    ],
    efforts: [
      { id: 'low', name: 'Low' },
      { id: 'medium', name: 'Medium' },
      { id: 'high', name: 'High' },
    ],
    // What a chat on this provider runs as unless it is changed. The New-chat
    // sheet preselects these and shows them as one line rather than a wall of
    // chips, and a chat started on them says nothing about either on the wire —
    // a default is what the host already does.
    default_model: 'sonnet',
    default_effort: 'medium',
    // When the sign-in runs out. Two days off by default, so the page's warning
    // threshold can be moved past by signing in again rather than by waiting.
    // `days_left` is whole days rounded down and goes negative once it has
    // gone; `source` is which credential the host is reading and is not
    // something a page draws.
    login: { expires_at: null, days_left: null, source: 'credentials-file' },
    usage: {
      provider: 'claude-code',
      seen_at: new Date().toISOString(),
      session_id: null,
      status: 'allowed',
      is_using_overage: false,
      five_hour: { utilization: 0.25, resets_at: new Date(Date.now() + 9e6).toISOString(), resets_at_epoch: Math.floor((Date.now() + 9e6) / 1000) },
      seven_day: { utilization: 0.41, resets_at: new Date(Date.now() + 4e8).toISOString(), resets_at_epoch: Math.floor((Date.now() + 4e8) / 1000) },
    },
  },
  // Codex, wired and signed out, in the shape the host's runtime quoted
  // (2026-10-01): models with their own efforts and the default flagged on its
  // entry, and a `login` block that never carries an expiry. Signing in on the
  // Providers page is a device code here, and the fixture plays the person
  // entering it on OpenAI's page eight seconds after it is handed out.
  {
    id: 'codex',
    name: 'Codex',
    sign_in: { kind: 'device-code', start: '/v1/providers/codex/login', status: '/v1/providers/codex/login', cancel: '/v1/providers/codex/login', code: null, sign_out: null },
    connected: false,
    reason: 'no login on the box',
    models: [
      { id: 'gpt-6.1-sol', name: 'GPT-6.1-Sol', default: true, efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
      { id: 'gpt-6-luna', name: 'GPT-6-Luna', default: false, efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
    ],
    efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    usage: null,
    login: { expires_at: null, days_left: null, source: 'none', account: null, plan: null, status: 'Not logged in' },
  },
  { id: 'grok', name: 'Grok', sign_in: null, connected: false, reason: 'not wired' },
  { id: 'opencode', name: 'OpenCode', sign_in: null, connected: false, reason: 'not wired' },
]

// The agents the host has, as objects: an id a session is started by, a name a
// person is shown, and the agent's own mark or nothing. Two of them share a
// first letter on purpose — that is what makes the page's avatar rule ("Sc" for
// Scout) visible at all, and it was invisible while every agent here began with
// a different letter.
const AGENTS = [
  { id: 'alfred', name: 'Alfred', emoji: '🧰' },
  { id: 'beatrice', name: 'Beatrice', emoji: '📐' },
  { id: 'sable', name: 'Sable', emoji: null },
  { id: 'scout', name: 'Scout', emoji: '🔭' },
]

const fakeSchedules = createFakeSchedules(AGENTS.map((agent) => agent.id))
const activity = activityFixture(AGENTS, fakeSchedules)
const scheduleFiles = scheduleFilesFixture(fakeSchedules)

// When the wired provider's sign-in runs out, and the sign-in flow while one is
// going. Two days off to begin with, which is inside the page's warning
// threshold — so the warning is a thing that can be looked at, and signing in
// again is what takes it away.
let signedInUntil = Date.now() + 2 * 86400000
let flow = null
let codexFlow = null

// Codex's device-code flow, as the host answers it: a URL, a one-time code,
// and a state that turns `done` by itself once the code is entered.
function codexAnswer() {
  const codex = PROVIDERS.find((p) => p.id === 'codex')
  if (codexFlow?.state === 'pending' && Date.now() - codexFlow.at > 8000) {
    codex.connected = true
    codex.reason = null
    codex.login = { expires_at: null, days_left: null, source: 'chatgpt', account: 'you@example.invalid', plan: 'Plus', status: 'Logged in using ChatGPT' }
    codexFlow = { ...codexFlow, state: 'done' }
  }
  if (!codexFlow) return { provider: 'codex', kind: 'device-code', accepts_code: false, state: 'idle', url: null, code: null, login: codex.login }
  const { at, ...answer } = codexFlow
  return { ...answer, provider: 'codex', kind: 'device-code', accepts_code: false, login: codex.login }
}

// The `login` block on the wired row, read fresh on every call the way the real
// host reads it. `days_left` is whole days rounded down and goes negative once
// the login has gone.
function withLogin(provider) {
  if (!provider.login || provider.id !== 'claude-code') return provider
  const ms = signedInUntil - Date.now()
  return {
    ...provider,
    login: {
      ...provider.login,
      expires_at: new Date(signedInUntil).toISOString(),
      days_left: Math.floor(ms / 86400000),
    },
  }
}

function flowAnswer() {
  const provider = withLogin(PROVIDERS.find((p) => p.id === 'claude-code'))
  const login = { ...provider.login, connected: provider.connected, reason: provider.reason }
  if (!flow) return { provider: 'claude-code', kind: 'paste-code', accepts_code: false, state: 'idle', url: null, code: null, instructions: null, message: null, started_at: null, finished_at: null, login }
  return { ...flow, provider: 'claude-code', kind: 'paste-code', accepts_code: flow.state === 'pending', login }
}

const sessions = new Map()
const listeners = new Map() // session id -> Set of response objects
const watchers = new Set() // { res, archived } — everyone watching the whole list

function record(id, agent, provider) {
  const now = new Date().toISOString()
  return {
    session_id: id,
    agent,
    provider,
    workspace: `/workspaces/${agent}`,
    created: now,
    last_activity: now,
    // When it last showed a sign of life. Null until it has had an event, and
    // not the same thing as `last_activity`, which housekeeping also bumps.
    last_event_at: null,
    state: 'running',
    // What the session *is*, as against whether a process exists. The page
    // reads this and never derives it from the event stream.
    status: 'idle',
    title: null,
    last_message: null,
    pinned: false,
    archived: false,
    // What this chat is set to run as, and `null` for the agent's own default —
    // which the page shows as a default and never as a name it invented.
    model: null,
    effort: null,
    // How many background tasks the session is waiting on. An integer, `0` when
    // there is none, never absent — a page reads it and counts nothing.
    background_tasks: 0,
    // How big an imported conversation's history is, for the one thing a page
    // has to say before a person replies to one: the first message forks it and
    // sends that history once. `null` on everything else, and on an imported
    // one the moment it has been written into.
    history_bytes: null,
    // Where the session's log ends. It is the record's number and never an
    // event's identity, which is why it is not called `seq`.
    record_seq: 0,
    // How far it has been read. Unread is `seen_seq < record_seq` and it is
    // the host's fact, not the browser's: one person, one box, one broker.
    seen_seq: 0,
    pending_permissions: [],
    turns: [],
  }
}

function emit(id, event) {
  const s = sessions.get(id)
  s.record_seq += 1
  const now = new Date().toISOString()
  s.last_activity = now
  s.last_event_at = now
  const withSeq = { ...event, seq: s.record_seq, session_id: id }
  s.events.push(withSeq)
  noteStatus(s, event)
  if (event.type === 'broker.permission_request') s.pending_permissions.push(event)
  for (const res of listeners.get(id) ?? []) res.write(`data: ${JSON.stringify(withSeq)}\n\n`)
  return s.record_seq
}

// The one place a status is decided, and it is decided here rather than on the
// page for the reason the interface gives: a page had to guess `working` from
// "an event arrived recently" and could not tell a paused turn from a slow one.
function noteStatus(s, event) {
  if (event.type === 'broker.user_message') {
    s.status = 'working'
    // Until the titler has run, the title is the opening words of the first
    // message, cut at a word. A session is never untitled.
    if (!s.title && typeof event.text === 'string') s.title = openingWords(event.text)
  } else if (event.type === 'broker.permission_request') {
    // A permission outranks a turn in flight: a spinner would hide the one
    // thing the person has to do for it to continue.
    s.status = 'needs_input'
  } else if (event.type === 'broker.permission_resolved') {
    s.status = 'working'
  } else if (event.type === 'assistant' && !event.parent_tool_use_id) {
    const text = (event.message?.content ?? [])
      .filter((b) => b?.type === 'text')
      .map((b) => b.text)
      .join(' ')
      .trim()
    if (text) s.last_message = text.length > 180 ? `${text.slice(0, 180)}…` : text
  } else if (event.type === 'result') {
    // A good result with background work still going is `waiting`, not
    // `done`: the turn is over and more output is coming.
    s.status = event.is_error ? 'failed' : s.background_tasks > 0 ? 'waiting' : 'done'
  } else if (event.type === 'broker.exit') {
    s.state = 'exited'
    // An ended session is normally `done`: it has an answer to read and its id
    // resumes. Only a turn that was in flight when the process went is a failure.
    if (s.status === 'working' || s.status === 'needs_input') s.status = 'failed'
  }
}

function openingWords(text) {
  const clean = text.trim().replace(/\s+/g, ' ')
  if (clean.length <= 40) return clean
  return `${clean.slice(0, 40).replace(/\s+\S*$/, '')}…`
}

// The session record, to everyone watching. It is a snapshot and not an event:
// it spends no number, it is never written to the log, and its `record_seq` is
// where the log has got to rather than what this item is. Sent first on every
// stream, and again whenever anything about the session changes.
function snapshot(id) {
  const s = sessions.get(id)
  if (!s) return
  const line = snapshotLine(s)
  for (const res of listeners.get(id) ?? []) res.write(line)
  // The live list gets every record change, archived or not — a page filtering
  // archived rows still has to be told the moment a row becomes archived, or it
  // keeps drawing one.
  for (const w of watchers) w.res.write(line)
}

function snapshotLine(s) {
  return `data: ${JSON.stringify({ type: 'broker.session', ...publicRecord(s) })}\n\n`
}

// The arithmetic the real host does, and the app must not: `total_cost_usd` is
// the whole process's running total and resets when a session is resumed;
// `cost_delta_usd` is what *this* turn cost, which is the total minus the
// previous turn's within the same process. The numbers themselves are a
// stand-in — what is real here is that the two fields differ and that a resume
// starts the total again while the deltas carry on meaning the same thing.
function recordTurn(s, event) {
  const delta = Number((0.0413 / (s.turns.length + 1)).toFixed(8))
  s.processTotal = Number((s.processTotal + delta).toFixed(8))
  // What the model's context held at this turn's last request, and how much of
  // it that model has. It is on the turn record **and** on the `result` that
  // closes the turn, so a page can price the turn it has just watched end
  // without waiting for the record.
  const context = { used: 26000 + s.turns.length * 9271, window: 1000000 }
  const turn = {
    model: 'claude-opus-5',
    usage: event.usage ?? {},
    context,
    total_cost_usd: s.processTotal,
    cost_delta_usd: delta,
    duration_ms: event.duration_ms ?? null,
    num_turns: event.num_turns ?? null,
    permission_denials: event.permission_denials ?? [],
  }
  s.turns.push(turn)
  return { ...event, total_cost_usd: turn.total_cost_usd, context }
}

// What the session has running in the background, to everyone watching. The
// count is on the record and the names are on the event, which is sent when the
// count changes and at no other time.
function setBackground(id, tasks) {
  const s = sessions.get(id)
  if (!s || s.background_tasks === tasks.length) return
  s.background_tasks = tasks.length
  // The last task in is what ends a wait, as it does on the host.
  if (tasks.length === 0 && s.status === 'waiting') s.status = 'done'
  emit(id, { type: 'broker.background_tasks', count: tasks.length, tasks })
  snapshot(id)
}

// How long a turn's one remaining background task outlives the turn, so the
// `waiting` state can be looked at in a browser. `FAKE_WAIT_MS` moves it.
const WAIT_MS = Number(process.env.FAKE_WAIT_MS ?? 4000)
// How fast the recording plays: `1` is something like a real session's pace,
// and the tool's own tests play it at a fraction of that.
const PACE = Number(process.env.FAKE_PACE ?? 1)

const BACKGROUND_TASKS = [
  { task_id: 'bg-1', task_type: 'local_agent', description: 'Reading the week’s notes for anything unfinished' },
  { task_id: 'bg-2', task_type: 'local_agent', description: 'Checking the deploy that went out this morning' },
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// The recording is played more than once, and a real session never says the
// same message id or tool_use id twice. Without this every turn after the
// first would be read as a repeat of the first and drawn only once.
function freshIds(event, turn) {
  return JSON.parse(JSON.stringify(event).replace(/"(msg_[a-z0-9_]+|toolu_[a-z0-9_]+)"/g, (_, id) => `"${id}_t${turn}"`))
}

// Play the recording back at something like the pace a real one arrives at,
// so the page's streaming is visible rather than instantaneous.
async function play(id, { fromIndex = 0, toIndex = RECORDING.length } = {}) {
  const s = sessions.get(id)
  s.plays += 1
  const turn = s.plays
  // A turn is in flight from the moment it starts, not from the moment the
  // model answers — so the row breathes on send.
  s.status = 'working'
  snapshot(id)
  // Two background tasks for the length of the turn, so the thread's chip and
  // the row's kicker are things that can be looked at in a browser rather than
  // only asserted. A real session's count moves when its own tasks do.
  setBackground(id, BACKGROUND_TASKS)
  for (let i = fromIndex; i < toIndex; i += 1) {
    if (!sessions.has(id) || s.stopped) return
    const { seq, session_id, ...event } = freshIds(RECORDING[i], turn)
    if (event.type === 'broker.session') continue // this broker sends its own
    if (event.type === 'broker.exit') return // left for DELETE to do
    // The person's own message and the answer to a permission are written when
    // they happen, not replayed from a recording of someone else's.
    if (event.type === 'broker.user_message' || event.type === 'broker.permission_resolved') continue
    if (event.type === 'broker.permission_request') {
      // A request id is asked once and answered once; replaying the recording
      // must not ask the same question twice under the same name.
      s.asked += 1
      const asking = { ...event, request_id: `${event.request_id}-${s.asked}` }
      emit(id, asking)
      snapshot(id) // the status became `needs_input`, and the home screen wants it
      s.waitingFor = asking.request_id
      while (s.waitingFor && sessions.has(id) && !s.stopped) await sleep(100) // the turn is paused
      continue
    }
    if (event.type === 'result') {
      // One of the two background tasks is done with the turn; the other
      // outlives it, so the session ends the turn `waiting` with one task, and
      // is `done` when that one comes in. Each count is an event like any other.
      setBackground(id, BACKGROUND_TASKS.slice(0, 1))
      // The turn record is written as the turn ends, and the record goes out
      // again straight after it — which is where the page reads the turn's
      // cost and model from.
      emit(id, recordTurn(s, event))
      snapshot(id)
      setTimeout(() => {
        if (sessions.has(id) && !s.stopped && s.plays === turn) setBackground(id, [])
      }, WAIT_MS)
    } else {
      emit(id, event)
      if (event.type === 'assistant' && !event.parent_tool_use_id) snapshot(id)
    }
    await sleep((event.type === 'stream_event' ? 90 : 350) * PACE)
  }
}

// **A question, in the one shape every provider's question arrives in.** The
// host turns Claude Code's `AskUserQuestion` and Codex's own asking into this,
// so the page reads one form and no vendor's. A message that is `/ask <shape>`
// raises one of these instead of playing the recording — `/ask` alone is the
// single choice — and the answer body the page sends is kept in `answered`,
// readable at `GET /v1/fake/answers`, so what the page sent can be looked at
// rather than assumed.
const QUESTIONS = {
  single: {
    message: null,
    questions: [
      {
        // A Claude question's id is its full question text, as the host sends it.
        id: 'What should the plugin be called? The id follows from it and cannot change again once listed.',
        header: 'Name',
        question: 'What should the plugin be called? The id follows from it and cannot change again once listed.',
        options: [
          { label: 'Parlance (Recommended)', description: 'Your way of speaking, which is what the terms are.', preview: null },
          { label: 'Lexicon', description: 'Plain, and already taken by two other plugins.', preview: null },
          { label: 'Glossa', description: 'Short, and nobody will know how to say it.', preview: 'id: glossa\nname: Glossa' },
        ],
        multi: false, other: true, secret: false, required: true,
      },
    ],
  },
  multi: {
    message: null,
    questions: [
      {
        id: 'Which checks should run before a deploy?',
        header: 'Checks',
        question: 'Which checks should run before a deploy?',
        options: [
          { label: 'Tests', description: 'npm test', preview: null },
          { label: 'Lint', description: null, preview: null },
          { label: 'Image build', description: 'Builds the Dockerfile from the named commit.', preview: null },
        ],
        multi: true, other: true, secret: false, required: true,
      },
    ],
  },
  text: {
    message: 'One thing before I start.',
    questions: [
      { id: 'q1', header: 'Directory', question: 'Which directory should the new notes go in?', options: [], multi: false, other: true, secret: false, required: true },
    ],
  },
  secret: {
    message: null,
    questions: [
      { id: 'q1', header: 'Passphrase', question: 'The key is locked. What is its passphrase?', options: [], multi: false, other: true, secret: true, required: true },
    ],
  },
  three: {
    message: 'A few choices about the release.',
    questions: [
      {
        id: 'q1', header: 'Channel', question: 'Which channel does this go out on?',
        options: [{ label: 'Stable', description: null, preview: null }, { label: 'Beta (Recommended)', description: 'Two people are on it.', preview: null }],
        multi: false, other: false, secret: false, required: true,
      },
      {
        id: 'q2', header: 'Notes', question: 'Which sections should the release notes have?',
        options: [{ label: 'Fixes', description: null, preview: null }, { label: 'Features', description: null, preview: null }, { label: 'Known issues', description: null, preview: null }],
        multi: true, other: true, secret: false, required: true,
      },
      { id: 'q3', header: 'Codename', question: 'Anything to call it? Leave it empty for none.', options: [], multi: false, other: true, secret: false, required: false },
    ],
  },
  // An elicitation, as the host turns one into a question: its message above
  // the questions, a boolean as the options Yes and No, and a field that may
  // be left out of the answer altogether.
  form: {
    message: 'The release tool wants two things before it publishes.',
    questions: [
      {
        id: 'publish', header: 'Publish', question: 'Publish the release notes as well?',
        options: [{ label: 'Yes', description: null, preview: null }, { label: 'No', description: null, preview: null }],
        multi: false, other: false, secret: false, required: true,
      },
      { id: 'note', header: 'Note', question: 'A line for the changelog, if you want one.', options: [], multi: false, other: true, secret: false, required: false },
    ],
  },
}

// What the broker checks before an answer leaves pending: every required
// question answered, an optional one either answered or left out, no two typed
// values on one question, a typed value only where `other` allows one, one
// value on a one-choice question. A sentence naming the field, or null.
function refusal(asked, body) {
  if (body.behavior !== 'allow') return null
  const answers = body.answers ?? {}
  if (typeof answers !== 'object' || Array.isArray(answers)) return 'answers must be an object of question id to a list of strings'
  const ids = new Set(asked.questions.map((q) => q.id))
  for (const key of Object.keys(answers)) if (!ids.has(key)) return `answers.${key} is not a question on this request`
  for (const q of asked.questions) {
    if (!(q.id in answers)) {
      if (q.required) return `answers.${q.id} is required and has no answer`
      continue
    }
    const values = answers[q.id]
    if (!Array.isArray(values) || values.some((v) => typeof v !== 'string')) return `answers.${q.id} must be a list of strings`
    if (values.length === 0) return `answers.${q.id} has no answer; leave an optional question out instead`
    if (!q.multi && values.length > 1) return `answers.${q.id} takes one answer and was sent ${values.length}`
    const labels = new Set(q.options.map((o) => o.label))
    const typed = values.filter((v) => !labels.has(v))
    if (typed.length > 0 && !q.other) return `answers.${q.id} has a value that is not one of its options`
    if (typed.length > 1) return `answers.${q.id} carries ${typed.length} typed values and takes one`
  }
  return null
}

const answered = [] // every answer body the page sent, oldest first

// **A skill request**, as a persona asks to open a plugin skill: an approval
// whose tool is `Skill`, with the skill's long description where a tool's
// description goes. A message that is `/skill` raises one.
async function askSkill(id) {
  const s = sessions.get(id)
  s.asked = (s.asked ?? 0) + 1
  const request_id = `skill-${s.asked}`
  emit(id, {
    type: 'broker.permission_request',
    kind: 'approval',
    request_id,
    tool_name: 'Skill',
    display_name: 'Skill',
    input: { skill: 'pdf' },
    description:
      'Comprehensive PDF manipulation toolkit for extracting text and tables, creating new PDFs, merging and splitting documents, and handling forms. Use when the work needs to fill in a PDF form or programmatically process, generate or analyse PDF documents at scale.',
    decision_reason: '',
    tool_use_id: `toolu_skill_${s.asked}`,
  })
  snapshot(id)
  while (s.pending_permissions.some((x) => x.request_id === request_id) && !s.stopped) await sleep(100)
  if (!sessions.has(id) || s.stopped) return
  const allowed = answered.findLast((a) => a.request_id === request_id)?.body?.behavior === 'allow'
  emit(id, { type: 'assistant', message: { id: `msg-skill-${s.asked}`, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: allowed ? 'The skill is open.' : 'I will do without the skill.' }] } })
  emit(id, recordTurn(s, { type: 'result', subtype: 'success', is_error: false, duration_ms: 400, num_turns: 1, usage: { input_tokens: 10, output_tokens: 20 } }))
  snapshot(id)
}

const s0 = (id) => sessions.get(id)

// A request closed by the broker rather than answered: `expired`, `cancelled`,
// or `ended` with the process. The same event an answer is, with the behavior
// saying how.
function close(id, asked, behavior) {
  const s = sessions.get(id)
  s.pending_permissions = s.pending_permissions.filter((x) => x.request_id !== asked.request_id)
  if (s.waitingFor === asked.request_id) s.waitingFor = null
  emit(id, { type: 'broker.permission_resolved', request_id: asked.request_id, kind: asked.kind ?? 'approval', behavior, tool_name: asked.tool_name ?? null, message: null })
  if (behavior !== 'ended') snapshot(id)
}

function closeAll(id, behavior) {
  for (const asked of [...sessions.get(id).pending_permissions]) close(id, asked, behavior)
}

// **Three at once**, as the CLI raises them: a run of reads outside the
// agent's folders, each with the session rule the CLI proposes. A message that
// is `/ask several` raises them; the turn ends when all three have closed.
async function askSeveral(id) {
  const s = sessions.get(id)
  const ids = []
  const ws = s.workspace.replace(/\/[^/]+$/, '')
  for (const [tool, input, dir] of [
    ['Read', { file_path: `${ws}/shared/bin/broker` }, `${ws}/shared/bin`],
    ['Grep', { pattern: 'reap_idle', path: `${ws}/shared` }, `${ws}/shared`],
    ['Glob', { pattern: '**/app.js', path: `${ws}/projects` }, `${ws}/projects`],
  ]) {
    s.asked = (s.asked ?? 0) + 1
    const request_id = `several-${s.asked}`
    ids.push(request_id)
    emit(id, {
      type: 'broker.permission_request',
      kind: 'approval',
      request_id,
      tool_name: tool,
      display_name: tool,
      input,
      description: '',
      decision_reason: 'Path is outside allowed working directories',
      decision_reason_type: 'workingDir',
      permission_suggestions: [
        { type: 'addRules', rules: [{ toolName: tool, ruleContent: `/${dir}/**` }], behavior: 'allow', destination: 'session' },
        // The CLI proposes rules for the persona's own settings file too; the
        // broker refuses them, so the page must send only the session one.
        ...(tool === 'Grep' ? [{ type: 'addRules', rules: [{ toolName: tool, ruleContent: `/${dir}/**` }], behavior: 'allow', destination: 'localSettings' }] : []),
      ],
      tool_use_id: `toolu_several_${s.asked}`,
    })
  }
  snapshot(id)
  while (s.pending_permissions.some((x) => ids.includes(x.request_id)) && !s.stopped) await sleep(100)
  if (!sessions.has(id) || s.stopped) return
  emit(id, { type: 'assistant', message: { id: `msg-several-${s.asked}`, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: 'Done with the three.' }] } })
  emit(id, recordTurn(s, { type: 'result', subtype: 'success', is_error: false, duration_ms: 400, num_turns: 1, usage: { input_tokens: 10, output_tokens: 20 } }))
  snapshot(id)
}

async function ask(id, shape) {
  const s = sessions.get(id)
  const asked = QUESTIONS[shape] ?? QUESTIONS.single
  s.asked = (s.asked ?? 0) + 1
  const request_id = `question-${s.asked}`
  emit(id, { type: 'broker.permission_request', kind: 'question', request_id, tool_name: 'AskUserQuestion', provider: s.provider, ...asked })
  snapshot(id)
  // Waiting on its own request, not on `waitingFor`: a recording still playing
  // may be paused on a permission of its own at the same time.
  while (s.pending_permissions.some((x) => x.request_id === request_id) && !s.stopped) await sleep(100)
  if (!sessions.has(id) || s.stopped) return
  const body = answered.findLast((a) => a.request_id === request_id)?.body ?? {}
  const said = body.behavior === 'allow'
    ? `You answered: ${Object.entries(body.answers ?? {}).map(([q, v]) => `${q} = ${JSON.stringify(v)}`).join('; ')}.`
    : 'You declined to answer, so I will go on without it.'
  emit(id, { type: 'assistant', message: { id: `msg-ask-${s.asked}`, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: said }] } })
  emit(id, recordTurn(s, { type: 'result', subtype: 'success', is_error: false, duration_ms: 400, num_turns: 1, usage: { input_tokens: 10, output_tokens: 20 } }))
  snapshot(id)
}

// The one part of a `multipart/form-data` body this tool needs: the file's own
// name and how many bytes of it there are. Enough to answer like a broker and
// no more — a real one writes the bytes down, and this one is a recording.
function filePart(raw, contentType) {
  const boundary = /boundary=("?)([^";]+)\1/.exec(contentType)?.[2]
  const text = raw.toString('latin1')
  const name = /filename="([^"]*)"/.exec(text)?.[1] || 'file'
  if (!boundary) return { name, size: raw.length }
  const head = text.indexOf('\r\n\r\n')
  const tail = text.lastIndexOf(`\r\n--${boundary}`)
  const size = head === -1 || tail === -1 ? raw.length : Math.max(0, tail - (head + 4))
  return { name, size }
}

// Shared keys' made-up state, also read by tools/capture-accounts.mjs and tools/capture-settings.mjs.
const FAKE_KEYS = JSON.parse(readFileSync(new URL('./fake-keys.json', import.meta.url), 'utf8'))

function json(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body, null, 2))
}

function openStream(res) {
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' })
  // Both streams open the same way, and the opening comment states this
  // stream's own keepalive interval — so staleness is measured against the
  // number the host actually uses rather than one copied into a document.
  res.write(`: open ping=${PING_MS}\n\n`)
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://placeholder')
  const p = decodeURIComponent(url.pathname)

  if (p === '/up') return json(res, 200, { ok: true, service: 'fake-broker' })
  if (req.headers.authorization !== `Bearer ${TOKEN}`) return json(res, 401, { error: 'bearer token required' })

  // A file on its way to a session, before the body below reads anything as
  // JSON — an upload is the one request here that is not JSON.
  //
  // The real broker writes the bytes into the agent's workspace and answers with
  // the path it used. This one counts them, keeps nothing, and answers with the
  // path it would have used: what is being exercised is that the page uploads
  // through this app, holds a chip while it goes, and sends the host's path and
  // never the file.
  const upload = /^\/v1\/sessions\/([^/]+)\/attachments$/.exec(p)
  if (upload && req.method === 'POST' && sessions.has(upload[1])) {
    const s = sessions.get(upload[1])
    const raw = await new Promise((resolve) => {
      const chunks = []
      req.on('data', (c) => chunks.push(c))
      req.on('end', () => resolve(Buffer.concat(chunks)))
    })
    const { name, size } = filePart(raw, req.headers['content-type'] ?? '')
    if (size > MAX_ATTACHMENT) {
      return json(res, 413, { error: `A file can be at most ${Math.round(MAX_ATTACHMENT / (1024 * 1024))} MB.` })
    }
    // The host's own shape: a list, one entry per part, each saying where the
    // file is, what it ended up being called and what it is.
    const day = new Date().toISOString().slice(0, 10)
    return json(res, 201, {
      attachments: [{ path: `${s.workspace}/attachments/${day}/${name}`, name, size, type: null }],
    })
  }

  const body = await new Promise((resolve) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8')
      try { resolve(text === '' ? {} : JSON.parse(text)) } catch { resolve({}) }
    })
  })

  const live = [...sessions.values()].filter((s) => s.state === 'running').length

  // Both views post to the same Undo route. The schedule fake owns its IDs;
  // let it answer those before Activity's unknown-ID refusal can intercept.
  const scheduleUndo = /^\/v1\/activity\/([^/]+)\/undo$/.exec(p)
  if (req.method === 'POST' && scheduleUndo && fakeSchedules.activities.has(scheduleUndo[1])) {
    if (fakeSchedules.handle(req, res, url, body)) return
  }
  const activityAnswer = scheduleFiles.handle(req.method, url, body) ?? activity.handle(req.method, p, url, body) ?? activity.files(req.method, p, url)
  if (activityAnswer) return json(res, activityAnswer.status, activityAnswer.body)
  if (fakeSchedules.handle(req, res, url, body)) return

  if (p === '/v1/agents' && req.method === 'GET') {
    return json(res, 200, { agents: AGENTS }) // objects, sorted by id
  }

  // The host's numbers, which this app relays rather than keeping.
  if (p === '/v1/limits' && req.method === 'GET') {
    return json(res, 200, {
      attachment_max_bytes: MAX_ATTACHMENT,
      inline_image_max_bytes: 5 * 1024 * 1024,
      attachment_types: ['text/plain', 'text/markdown', 'text/csv', 'application/pdf', 'image/png', 'image/jpeg'],
    })
  }

  // Shared keys (2026-11-18): Settings › Accounts. Made-up state, no value.
  const keysRoute = /^\/v1\/keys(?:\/([^/]+))?(?:\/(check|plugins)(?:\/([^/]+))?)?$/.exec(p)
  if (keysRoute) {
    const [, id, what, plugin] = keysRoute
    if (!id) return json(res, 200, { providers: FAKE_KEYS })
    const row = FAKE_KEYS.find(k => k.id === id)
    if (!row) return json(res, 404, { error: `no provider '${id}'` })
    if (req.method === 'PUT' && !what) Object.assign(row, { set: true, source: 'saved', shown: { [row.fields[0].name]: '…9f2c' }, checked: { at: new Date().toISOString(), ok: true, why: `${row.name} accepted the key.` } })
    if (req.method === 'DELETE' && !what) Object.assign(row, { set: false, source: null, shown: {}, checked: null })
    if (what === 'plugins' && plugin) for (const u of row.plugins) if (u.id === plugin) u.uses = req.method === 'PUT' ? 'own' : 'shared'
    return json(res, 200, row)
  }

  if (p === '/v1/providers' && req.method === 'GET') {
    return json(res, 200, { providers: PROVIDERS.map(withLogin) })
  }

  // Renewing the sign-in. A flow that cannot finish without a person: a URL to
  // open, a code to paste back, and four routes round it.
  const loginRoute = /^\/v1\/providers\/([^/]+)\/login$/.exec(p)
  if (loginRoute) {
    const provider = PROVIDERS.find((x) => x.id === loginRoute[1])
    if (provider?.id === 'codex') {
      if (req.method === 'POST') {
        if (codexAnswer().state === 'pending') return json(res, 200, codexAnswer())
        codexFlow = {
          state: 'pending',
          url: 'https://example.invalid/codex/device',
          code: 'FAKE-12345',
          instructions: 'Open the URL, sign in to ChatGPT, and enter the one-time code. This page notices the login by itself: GET this route again for the state.',
          expires_at: new Date(Date.now() + 15 * 60000).toISOString(),
          at: Date.now(),
        }
        return json(res, 201, codexAnswer())
      }
      if (req.method === 'GET') return json(res, 200, codexAnswer())
      if (req.method === 'DELETE') {
        codexFlow = null
        return json(res, 200, codexAnswer())
      }
    }
    // A sign-in asked for on a provider that is not wired is a 404.
    if (!provider?.connected) return json(res, 404, { error: `${loginRoute[1]} has no sign-in to renew.` })
    if (req.method === 'POST') {
      // Idempotent while pending: the same URL and the same `started_at`, never
      // a second flow — starting one would invalidate the code the person is
      // holding.
      if (flow?.state === 'pending') return json(res, 200, flowAnswer())
      flow = {
        state: 'pending',
        url: 'https://example.invalid/oauth/authorize?state=fake-broker',
        code: null,
        instructions: 'Open the URL, sign in, and copy the code the page gives you.',
        message: null,
        started_at: new Date().toISOString(),
        finished_at: null,
      }
      return json(res, 201, flowAnswer())
    }
    if (req.method === 'GET') return json(res, 200, flowAnswer())
    if (req.method === 'DELETE') {
      flow = null
      return json(res, 200, flowAnswer())
    }
  }

  const loginCode = /^\/v1\/providers\/([^/]+)\/login\/code$/.exec(p)
  if (loginCode && req.method === 'POST') {
    // Codex is entered on OpenAI's page, never pasted back here.
    if (loginCode[1] === 'codex') return json(res, 405, { error: 'Codex signs in by device code; there is no code to post.' })
    if (typeof body.code !== 'string' || body.code.trim() === '') return json(res, 400, { error: 'A code is required.' })
    // A code posted when nothing is waiting for one: the flow timed out, or was
    // called off. Start another.
    if (flow?.state !== 'pending') return json(res, 409, { error: 'No sign-in is waiting for a code. Start one.' })
    // One code this fixture refuses, so the failed state can be looked at.
    if (body.code.trim() === 'no') {
      flow = { ...flow, state: 'failed', message: 'Login failed: Request failed with status code 400', finished_at: new Date().toISOString() }
      return json(res, 200, flowAnswer())
    }
    signedInUntil = Date.now() + 30 * 86400000
    flow = { ...flow, state: 'done', message: null, finished_at: new Date().toISOString() }
    return json(res, 200, flowAnswer())
  }

  if (p === '/v1/sessions' && req.method === 'GET') {
    return json(res, 200, listing(url))
  }

  if (p === '/v1/sessions/events' && req.method === 'GET') {
    const archived = url.searchParams.get('archived') === '1'
    openStream(res)
    // The listing as it stands, so a page that connects before it fetches
    // anything already has the screen.
    for (const s of ordered(archived)) res.write(snapshotLine(s))
    const watcher = { res, archived }
    watchers.add(watcher)
    req.on('close', () => watchers.delete(watcher))
    return
  }

  // Mark every non-archived session read to its end. Archived ones are
  // deliberately untouched, and the answer is how many actually moved.
  if (p === '/v1/sessions/seen' && req.method === 'POST') {
    let marked = 0
    for (const s of sessions.values()) {
      if (s.archived || s.seen_seq >= s.record_seq) continue
      s.seen_seq = s.record_seq
      marked += 1
      snapshot(s.session_id)
    }
    return json(res, 200, { marked })
  }

  if (p === '/v1/sessions' && req.method === 'POST') {
    const provider = body.provider ?? 'claude-code'
    const row = PROVIDERS.find((p) => p.id === provider)
    if (provider !== 'claude-code' && provider !== 'codex') {
      return json(res, 400, { error: `Sessions run on Claude Code or Codex; ${provider} is not wired.` })
    }
    if (!row.connected) return json(res, 400, { error: `${row.name} is not signed in on this box.` })
    if (body.resume && sessions.has(body.session_id)) {
      const s = sessions.get(body.session_id)
      s.state = 'running'
      s.stopped = false
      s.status = 'idle'
      s.processTotal = 0 // a new process: the running total starts again
      json(res, 201, publicRecord(s))
      snapshot(body.session_id)
      emit(body.session_id, { type: 'system', subtype: 'init', model: 'claude-opus-5', cwd: s.workspace })
      return
    }
    if (live >= CAP) {
      return json(res, 429, { error: `At most ${CAP} sessions run at once, and ${live} are running. End one to start another — its id still resumes.`, cap: CAP, live })
    }
    // A model named at the start is the chat's from the start. Nothing named is
    // the agent's own default, which is the absence of a model and not a value.
    const models = PROVIDERS.find((p) => p.id === provider)?.models ?? []
    if (body.model !== undefined && body.model !== null && !models.some((m) => m.id === body.model)) {
      return json(res, 400, { error: `${provider} does not offer model "${body.model}".` })
    }
    const id = `s-${Math.random().toString(36).slice(2, 8)}`
    const s = { ...record(id, body.agent ?? 'alfred', provider), events: [], stopped: false, waitingFor: null, processTotal: 0, asked: 0, plays: 0 }
    if (body.model) s.model = body.model
    if (body.effort) s.effort = body.effort
    sessions.set(id, s)
    json(res, 201, publicRecord(s))
    snapshot(id)
    // Both recorded turns, one after the other: the subagent and the permission
    // first, then the turn with five calls in a row.
    play(id, { toIndex: RUN_TURN }).then(() => play(id, { fromIndex: RUN_TURN }))
    return
  }

  const one = /^\/v1\/sessions\/([^/]+)$/.exec(p)
  if (one && sessions.has(one[1])) {
    const s = sessions.get(one[1])
    if (req.method === 'GET') return json(res, 200, publicRecord(s))
    if (req.method === 'PATCH') {
      const change = patched(s, body)
      if (change.error) return json(res, 400, { error: change.error })
      s.last_activity = new Date().toISOString()
      json(res, 200, publicRecord(s))
      snapshot(one[1])
      return
    }
    if (req.method === 'DELETE') {
      s.stopped = true
      json(res, 200, { ended: true })
      // Nothing is asking once the process has gone, and the log says so for
      // each request before the exit — as the real broker does from the restart
      // that carries its half of this.
      closeAll(one[1], 'ended')
      emit(one[1], { type: 'broker.exit', exit_code: 0 })
      snapshot(one[1])
      return
    }
  }

  const events = /^\/v1\/sessions\/([^/]+)\/events$/.exec(p)
  if (events && req.method === 'GET' && sessions.has(events[1])) {
    const s = sessions.get(events[1])
    const from = url.searchParams.get('from')
    const last = url.searchParams.get('last')
    const before = url.searchParams.get('before')
    // `before=` without a `last=`, or a `last=` that is not a number or is
    // below 1, is a 400 — before a stream is opened, because a refusal is a
    // JSON answer and not an event.
    if (before !== null && last === null) return json(res, 400, { error: 'before= needs a last=' })
    if (last !== null && !(Number(last) >= 1)) return json(res, 400, { error: 'last= must be a number of turns, 1 or more' })

    if (last !== null) {
      // A window of turns. A turn runs from a `broker.user_message` — or from
      // the `system`/`init` that starts one — through its `result`, and the
      // count is in turns because that is the host's unit and not the page's.
      const ceiling = before === null ? Infinity : Number(before)
      const below = s.events.filter((e) => e.seq < ceiling)
      const starts = below.filter(isTurnStart).map((e) => e.seq)
      const take = Number(last)
      const firstSeq = starts.length <= take ? (below[0]?.seq ?? ceiling) : starts[starts.length - take]
      const window = below.filter((e) => e.seq >= firstSeq)
      openStream(res)
      res.write(
        `data: ${JSON.stringify({
          type: 'broker.session',
          ...publicRecord(s),
          // Where this answer starts and how much is above it. They describe
          // the answer and never the session, so they are on this snapshot
          // and on no other.
          window_first_seq: window[0]?.seq ?? null,
          turns_before: Math.max(0, starts.length - take),
        })}\n\n`,
      )
      for (const event of window) res.write(`data: ${JSON.stringify(event)}\n\n`)
      // One page of history and nothing more: it does not stay open and it is
      // not a second live stream.
      if (before !== null) return res.end()
      if (s.state !== 'running') return res.end()
      if (!listeners.has(events[1])) listeners.set(events[1], new Set())
      listeners.get(events[1]).add(res)
      req.on('close', () => listeners.get(events[1])?.delete(res))
      return
    }

    openStream(res)
    // The record first, then everything after `from` from the log, then live.
    res.write(snapshotLine(s))
    for (const event of s.events) {
      if (from === null || event.seq > Number(from)) res.write(`data: ${JSON.stringify(event)}\n\n`)
    }
    // An ended session replays its log and the stream ends where the log ends.
    // It starts nothing and nothing more will arrive on it, which is what the
    // page has to settle at rather than reconnect to.
    if (s.state !== 'running') return res.end()
    if (!listeners.has(events[1])) listeners.set(events[1], new Set())
    listeners.get(events[1]).add(res)
    req.on('close', () => listeners.get(events[1])?.delete(res))
    return
  }

  const messages = /^\/v1\/sessions\/([^/]+)\/messages$/.exec(p)
  if (messages && req.method === 'POST' && sessions.has(messages[1])) {
    const id = messages[1]
    // The CLI never echoes a typed message, so the person's own words go on
    // the record here — before anything the session says back, so a replay
    // reads in the order the conversation happened. The answer carries the seq
    // that event has, which is how a page keeps its own line and this event to
    // one line.
    // What the message carried goes on the record with it, so a chat reopened
    // later draws the same chips from the log rather than from the browser that
    // sent it.
    // Paths in, objects out: the messages route takes the paths the uploads
    // answered with, and the record carries the files themselves.
    const paths = Array.isArray(body.attachments) ? body.attachments.filter((x) => typeof x === 'string') : []
    const attachments = paths.map((path) => ({
      path,
      name: path.split('/').pop(),
      size: null,
      type: null,
    }))
    const said = { type: 'broker.user_message', text: body.text ?? '' }
    const seq = emit(id, attachments.length === 0 ? said : { ...said, attachments })
    // The row starts breathing on send and not when the model answers.
    snapshot(id)
    // 200: by the time this answers, the message is in the log and on the
    // session's input. Nothing was queued and nothing was created at a URL.
    json(res, 200, { accepted: true, seq })
    const asking = /^\/ask(?:\s+(\w+))?\s*$/.exec(body.text ?? '')
    if (asking?.[1] === 'several') askSeveral(id)
    else if (asking) ask(id, asking[1])
    // The two ways a request closes without an answer: nobody answered within
    // the broker's limit (`/expire`), or the CLI withdrew it (`/cancel`). Each
    // closes the oldest open request.
    else if (/^\/(expire|cancel)\s*$/.test(body.text ?? '')) {
      const r = s0(id).pending_permissions[0]
      if (r) close(id, r, body.text.trim() === '/expire' ? 'expired' : 'cancelled')
    }
    else if (/^\/skill\s*$/.test(body.text ?? '')) askSkill(id)
    // A reply is the second recording: the turn with the run of five calls.
    else play(id, { fromIndex: RUN_TURN })
    return
  }

  const permission = /^\/v1\/sessions\/([^/]+)\/permissions\/([^/]+)$/.exec(p)
  if (permission && req.method === 'POST' && sessions.has(permission[1])) {
    const s = sessions.get(permission[1])
    const asked = s.pending_permissions.find((x) => x.request_id === permission[2])
    // A request this broker does not hold — answered already, expired, or gone
    // with the process that asked — is the real broker's `404`, in its words.
    if (!asked) return json(res, 404, { error: `no permission request ${permission[2]} is pending` })
    // A malformed answer is refused before anything is sent, and the question
    // is still pending: the page can answer it again.
    const refused = asked?.kind === 'question' ? refusal(asked, body) : null
    if (refused) {
      console.log(`answer to ${permission[2]} refused: ${refused}`)
      return json(res, 400, { error: refused })
    }
    // `updatedPermissions` as the real broker reads it: the request's own
    // suggestions, on an allow, and only those whose destination is `session`
    // — a tap on a card never writes a persona's settings file. In its words.
    const updates = body.updatedPermissions
    if (updates !== undefined && updates !== null) {
      if (body.behavior !== 'allow' || !Array.isArray(updates) || !updates.every((u) => u && typeof u === 'object' && !Array.isArray(u))) {
        return json(res, 400, { error: "`updatedPermissions` is a list of the request's own permission_suggestions, on an allow" })
      }
      if (updates.some((u) => u.destination !== 'session')) {
        return json(res, 400, { error: "`updatedPermissions` may only carry suggestions whose destination is 'session'" })
      }
    }
    s.pending_permissions = s.pending_permissions.filter((x) => x.request_id !== permission[2])
    if (s.waitingFor === permission[2]) s.waitingFor = null
    const kind = asked?.kind === 'question' ? 'question' : 'approval'
    answered.push({ session_id: permission[1], request_id: permission[2], kind, body })
    console.log(`answer to ${permission[2]}: ${JSON.stringify(body)}`)
    json(res, 200, { behavior: body.behavior })
    // For the client that did not answer: the second device watching drops its
    // card instead of holding a question that no longer exists. A question's
    // carries what was chosen, so the thread can say it.
    emit(permission[1], {
      type: 'broker.permission_resolved',
      request_id: permission[2],
      kind,
      behavior: body.behavior,
      tool_name: asked?.tool_name ?? null,
      // An allow that carried rules is an allow for the rest of this chat.
      ...(body.behavior === 'allow' && Array.isArray(body.updatedPermissions) && body.updatedPermissions.length > 0 ? { scope: 'session', updatedPermissions: body.updatedPermissions } : {}),
      message: body.behavior === 'deny' ? (body.message ?? (kind === 'question' ? 'The person declined to answer.' : 'denied from the page')) : null,
      ...(kind === 'question' && body.behavior === 'allow' ? { answers: body.answers ?? {} } : {}),
    })
    snapshot(permission[1])
    return
  }

  // What the page sent when it answered, for a person checking it by hand. Not a
  // route the real broker has.
  if (p === '/v1/fake/answers' && req.method === 'GET') return json(res, 200, { answered })

  json(res, 404, { error: `no such route: ${req.method} ${p}` })
})

// A PATCH asking for nothing, a non-string title, or a non-boolean pin or
// archive is a 400. `{"title": null}` clears the title and is not nothing.
function patched(s, body) {
  let touched = false
  if ('title' in body) {
    if (body.title !== null && typeof body.title !== 'string') return { error: 'title must be a string or null' }
    s.title = body.title
    touched = true
  }
  for (const key of ['pinned', 'archived']) {
    if (key in body) {
      if (typeof body[key] !== 'boolean') return { error: `${key} must be true or false` }
      s[key] = body[key]
      touched = true
    }
  }
  // The model and the effort this chat runs at, on the record with the title and
  // the pin: a model chosen on the phone is the model on the laptop. `null` is
  // back to the agent's own default. Anything this provider does not offer is
  // refused in the host's own words, which is the sentence the page shows.
  for (const [key, offered] of [['model', 'models'], ['effort', 'efforts']]) {
    if (!(key in body)) continue
    if (body[key] === null) {
      s[key] = null
      touched = true
      continue
    }
    if (typeof body[key] !== 'string') return { error: `${key} must be a string or null` }
    const list = PROVIDERS.find((p) => p.id === s.provider)?.[offered] ?? []
    if (!list.some((c) => c.id === body[key])) {
      return { error: `${s.provider} does not offer ${key} "${body[key]}".` }
    }
    s[key] = body[key]
    touched = true
  }
  // How far this session has been read. The mark only ever goes up: a lower
  // value than the stored one is ignored and answered 200 with the record
  // unchanged, so a page scrolling back through history cannot mark a read
  // chat unread. Above `record_seq` is a 400 — that is a position the log does
  // not have.
  //
  // **Unless the request says `"unread": true`**, which is a person marking a
  // chat unread on purpose: the mark is lowered to the `seen_seq` sent, or one
  // short of the log's end when none is. A chat with nothing in it has nothing
  // to be unread and is a 400 saying so.
  if ('unread' in body && body.unread !== true) return { error: 'unread must be true' }
  if (body.unread === true) {
    if (s.record_seq <= 0) return { error: 'unread: nothing in it to be unread' }
    const to = 'seen_seq' in body ? body.seen_seq : s.record_seq - 1
    if (!Number.isFinite(to)) return { error: 'seen_seq must be a number' }
    if (to >= s.record_seq) return { error: `unread: seen_seq ${to} is not short of this session's log, which ends at ${s.record_seq}` }
    s.seen_seq = Math.max(0, Math.floor(to))
    touched = true
  } else if ('seen_seq' in body) {
    if (!Number.isFinite(body.seen_seq)) return { error: 'seen_seq must be a number' }
    if (body.seen_seq > s.record_seq) return { error: `seen_seq ${body.seen_seq} is past this session's log, which ends at ${s.record_seq}` }
    if (body.seen_seq > s.seen_seq) s.seen_seq = Math.floor(body.seen_seq)
    touched = true
  }
  if (!touched) return { error: 'nothing to change: send title, pinned, archived, model, effort, seen_seq or unread' }
  return {}
}

// Where a turn begins: what a person said, or the `system`/`init` of a session
// that starts a turn without one. The host's unit, stated once.
function isTurnStart(event) {
  return event.type === 'broker.user_message' || (event.type === 'system' && event.subtype === 'init')
}

function ordered(archived) {
  return [...sessions.values()]
    .filter((s) => Boolean(s.archived) === false || archived)
    .sort((a, b) => Date.parse(b.last_activity) - Date.parse(a.last_activity))
}

// `?agent=` filters the sessions and leaves `live` and `cap` alone: they
// describe the machine, not the filter. An archived session that is still
// running still counts against the cap, and saying so is the host's job —
// a page that hid it silently would be hiding a running agent.
function listing(url) {
  const archived = url.searchParams.get('archived') === '1'
  const agent = url.searchParams.get('agent')
  const list = ordered(archived).filter((s) => !agent || s.agent === agent)
  const hidden = [...sessions.values()].filter((s) => s.archived && s.state === 'running').length
  const live = [...sessions.values()].filter((s) => s.state === 'running').length
  return {
    sessions: list.map(publicRecord),
    // Two names for one number while the real box is between them: `live` is
    // what this listing has always carried and `live_count` is what it carries
    // now that each session says whether it is resident. The page reads
    // `live_count` first, so what is exercised here is the new field.
    live,
    live_count: live,
    cap: CAP,
    ...(hidden > 0 && !archived
      ? { note: `${hidden} archived ${hidden === 1 ? 'session is' : 'sessions are'} still running and still ${hidden === 1 ? 'counts' : 'count'} against the cap.` }
      : {}),
  }
}

// **`live` is on every record**, because a session costs memory on the host and
// "is this one actually in RAM" is what the page's Live filter asks. Here it is
// the same question as whether the process exists; on the real host it is the
// host's own bookkeeping and this app takes its word for it either way.
function publicRecord(s) {
  const { events, stopped, waitingFor, processTotal, asked, plays, ...rest } = s
  // `pending_permissions` goes out as the host sends it: request ids, not the
  // requests. The page read objects here for a day and dropped every live card.
  return { ...rest, pending_permissions: rest.pending_permissions.map((p) => p.request_id), live: s.state === 'running' }
}

// The sessions that were here before this process was: a home screen after a
// restart is mostly made of them. They are `exited`, they carry the title, the
// last thing said and the status the host backfills from the session's own
// durable log — the fields a page must be able to draw a row from — and they
// have a log, so opening one replays it and ends the stream where the log ends.
// A page that could only draw a session this broker itself started would look
// right here and be empty on the machine.
function seed({ id, agent, title, said, asked, minutesAgo, pinned = false, archived = false }) {
  const at = (mins) => new Date(Date.now() - mins * 60000).toISOString()
  const events = [
    { type: 'broker.user_message', session_id: id, text: asked, seq: 1 },
    {
      type: 'assistant',
      session_id: id,
      message: { id: `msg_seed_${id}`, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: said }] },
      seq: 2,
    },
    { type: 'result', session_id: id, subtype: 'success', is_error: false, duration_ms: 4200, num_turns: 1, total_cost_usd: 0.0271, usage: { input_tokens: 1840, output_tokens: 212 }, seq: 3 },
    { type: 'broker.exit', session_id: id, exit_code: 0, seq: 4 },
  ]
  sessions.set(id, {
    session_id: id,
    agent,
    provider: 'claude-code',
    workspace: `/workspaces/${agent}`,
    created: at(minutesAgo + 12),
    last_activity: at(minutesAgo),
    last_event_at: at(minutesAgo),
    state: 'exited',
    // An ended session is `done`, not `failed`: it has an answer to read and
    // its id resumes.
    status: 'done',
    title,
    // Already cut to a length the host chose, the way the interface says: the
    // page clamps by line in CSS and never truncates a second time.
    last_message: said.length > 180 ? `${said.slice(0, 180)}…` : said,
    pinned,
    archived,
    // Every record carries every key, seeded ones included: `null` means this
    // chat is on the agent's own default and never "the host is old".
    model: null,
    effort: null,
    background_tasks: 0,
    history_bytes: null,
    record_seq: events.length,
    seen_seq: 0,
    pending_permissions: [],
    turns: [
      {
        model: 'claude-opus-5',
        usage: { input_tokens: 1840, output_tokens: 212 },
        total_cost_usd: 0.0271,
        cost_delta_usd: 0.0271,
        duration_ms: 4200,
        num_turns: 1,
        permission_denials: [],
      },
    ],
    events,
    stopped: true,
    waitingFor: null,
    processTotal: 0.0271,
    asked: 0,
    plays: 0,
  })
}

// The personas answer in markdown, so one recorded answer is written the way a
// real one is — with the two payloads at the end of it, because "a reply cannot
// become HTML" is worth being able to see in a browser and not only in a test.
// The row's status box shows the same words with the marks taken off.
const MARKDOWN_REPLY = [
  '**Short answer:** the image was built from a commit the branch no longer had.',
  '',
  'What happened, in order:',
  '',
  '1. The branch was force-pushed while the build was reading it.',
  '2. `docker build` resolved a commit that had gone.',
  '3. The second try went through — see [the run](https://example.com/runs/412).',
  '',
  'The line that mattered:',
  '',
  '```sh',
  'git rev-parse HEAD   # a commit no ref pointed at any more',
  '```',
  '',
  // A table, for the same reason as the two payloads below: "a table renders
  // as a table" is worth being able to look at. Three columns, one of them
  // numbers the dashes say nothing about — so the page lines it up on the
  // right on its own — and a long cell, so a narrow window has something to
  // scroll sideways inside the bubble.
  'The three runs, and what each of them cost:',
  '',
  '| Run | Finished | Cost | What it did |',
  '| --- | :-: | --- | --- |',
  '| 412 | ✗ | $0.41 | Built from a commit the branch no longer had, and failed at the push step |',
  '| 413 | ✗ | $0.38 | The same, after the force-push had settled |',
  '| 414 | ✓ | $1.06 | Went through, on the commit that was actually there |',
  '',
  'It also came back with this in the log, which is text and stays text:',
  '<img src=x onerror=alert(1)> and <script>alert("xss")</script>',
].join('\n')

seed({
  id: 'older-1',
  agent: 'scout',
  title: 'The deploy that would not go',
  asked: 'why did the deploy fail last night?',
  said: MARKDOWN_REPLY,
  minutesAgo: 47,
  pinned: true,
})
seed({
  id: 'older-2',
  agent: 'alfred',
  title: 'Reading the week’s notes',
  asked: 'summarise this week',
  said: 'Three threads: the broker’s session interface, the design being built, and the fonts moving into the repo.',
  minutesAgo: 320,
})
// Nothing has been said in this one. `title: null` and `last_message: null`
// mean exactly that, and the page draws the empty session for them.
seed({ id: 'older-3', agent: 'sable', title: null, asked: '', said: '', minutesAgo: 1500 })
sessions.get('older-3').events = []
sessions.get('older-3').record_seq = 0
sessions.get('older-3').status = 'idle'
sessions.get('older-3').last_event_at = null
sessions.get('older-3').last_message = null
sessions.get('older-3').turns = []

// A long conversation: thirty turns of it, which is the thing a chat that opens
// at `?last=10` is for. Opening it draws the last ten turns at once and says
// there are twenty above; each page up fetches ten more, and the third of them
// reaches the beginning and the marker goes. Without a session this length the
// window is invisible here — every other seeded chat is one turn, which is a
// window of itself.
function seedLong({ id, agent, title, turns }) {
  const at = (mins) => new Date(Date.now() - mins * 60000).toISOString()
  const events = []
  const records = []
  let seq = 0
  for (let n = 1; n <= turns; n += 1) {
    const asked = `question ${n} of ${turns}: what happened in week ${n}?`
    const said = `Week ${n}, in short: the broker grew a route, the page read it, and nothing on the box moved that should not have. This is turn ${n}.`
    seq += 1
    events.push({ type: 'broker.user_message', session_id: id, text: asked, seq })
    seq += 1
    events.push({
      type: 'assistant',
      session_id: id,
      message: { id: `msg_long_${n}`, role: 'assistant', model: 'claude-opus-5', content: [{ type: 'text', text: said }] },
      seq,
    })
    seq += 1
    events.push({
      type: 'result',
      session_id: id,
      subtype: 'success',
      is_error: false,
      duration_ms: 3100 + n * 7,
      num_turns: 1,
      total_cost_usd: Number((0.0122 * n).toFixed(8)),
      usage: { input_tokens: 900 + n * 11, output_tokens: 120 + n },
      seq,
    })
    records.push({
      model: 'claude-opus-5',
      usage: { input_tokens: 900 + n * 11, output_tokens: 120 + n },
      total_cost_usd: Number((0.0122 * n).toFixed(8)),
      cost_delta_usd: 0.0122,
      duration_ms: 3100 + n * 7,
      num_turns: 1,
      permission_denials: [],
    })
  }
  sessions.set(id, {
    session_id: id,
    agent,
    provider: 'claude-code',
    workspace: `/workspaces/${agent}`,
    created: at(turns * 30),
    last_activity: at(90),
    last_event_at: at(90),
    state: 'exited',
    status: 'done',
    title,
    last_message: events.at(-2).message.content[0].text,
    pinned: false,
    archived: false,
    model: null,
    effort: null,
    background_tasks: 0,
    history_bytes: null,
    record_seq: seq,
    seen_seq: 0,
    pending_permissions: [],
    turns: records,
    events,
    stopped: true,
    waitingFor: null,
    processTotal: records.at(-1).total_cost_usd,
    asked: 0,
    plays: 0,
  })
}

seedLong({ id: 'older-4', agent: 'beatrice', title: 'A year of Mondays', turns: 30 })

// One of them is a conversation that began in the Claude Code app and has not
// been written into here yet. `history_bytes` is what the first reply to it
// costs to load once — the line above the composer is drawn from that number
// and from nothing else — and it goes `null` the moment the fork happens.
sessions.get('older-2').origin = 'claude-code-app'
sessions.get('older-2').history_bytes = 4812733

// A quiet stream says so at the interval it announced, so a page can tell a
// connection that has gone from one with nothing to say.
setInterval(() => {
  for (const set of listeners.values()) for (const res of set) res.write(': ping\n\n')
  for (const w of watchers) w.res.write(': ping\n\n')
}, PING_MS).unref?.()

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`fake-broker: listening on http://127.0.0.1:${server.address().port} (token: ${TOKEN === 'dev' ? 'dev' : 'from BROKER_TOKEN'})\n`)
})
