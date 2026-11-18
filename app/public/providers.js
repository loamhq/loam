import { formatTime, formatDate } from './time.js'
// The providers, and what their quota reading says in plain words.
//
// A provider is a vendor account a session runs on — Providers, that word, on
// the page and in the code. `GET /v1/providers` answers all of them, wired or
// not, because a row saying "Codex — not wired" is an answer where a missing
// row is a bug report.
//
// It touches no DOM and holds no clock of its own: `now` is passed in, so what
// a bar and a sentence say can be asserted in `node --test`.
//
// Three rules the host's interface gives, and all three are a test here:
//
// - **`utilization` is a fraction, not a percentage.** `0.25` is a quarter of
//   the window used, and a bar drawn at 25% is right where one drawn at 0.25%
//   is not.
// - **`usage` is `null` until a reading has been seen, and that is not a zeroed
//   page.** A provider that is not wired carries no `usage` key at all, so
//   there is no zeroed object to be drawn as a full quota bar either.
// - **Every window is read by name and rendered.** The host copies whatever
//   windows the provider reports rather than listing the ones it knows — a
//   fourth window appeared mid-session on a real log — so a page hard-coding
//   two would silently drop it. `five_hour` and `seven_day` lead; the rest
//   follow in the order the host sent them.

import { longAgo } from './sessions.js'

// The scalars that sit beside the windows in a `usage` object. Anything else
// whose value looks like a window is one.
// Codex's reading carries two more beside its windows, `credits` and
// `planType`, and neither is a window however much it looks like an object.
const NOT_A_WINDOW = new Set(['provider', 'seen_at', 'session_id', 'status', 'is_using_overage', 'credits', 'planType', 'source', 'account', 'buckets'])

// The two the interface names, in the design's words. Everything else is
// titled from its own key, because the page does not know what it will be.
const WINDOW_LABELS = { five_hour: '5-hour window', seven_day: 'Weekly' }
const LEAD = ['five_hour', 'seven_day']


// ------------------------------------------- what a provider offers a chat
//
// The models a session can be set to, and the efforts, **as the providers route
// offers them and nowhere else.** The design bundle names models per provider
// (Opus, Sonnet, Haiku for Claude Code) and those are its sample data: a model
// list is a fact about a vendor account, it changes without this app, and a
// list baked in here would be wrong the week a model is renamed. So a provider
// the host offers nothing for offers nothing on the page — no rows, and the
// menu says so — and a provider that is not wired offers nothing either.
//
// Two shapes are read for one reason. `GET /v1/agents` answered bare strings
// until it answered `{id, name, emoji}` objects, and the lesson written down
// from that change is that a page given only an id has to invent a display
// name. So an entry may be a string — the id, shown as it is — or an object
// with `id` and an optional `name`, and both land as the one shape the page
// draws. This is the single place either is read.
export function choicesOf(entries) {
  if (!Array.isArray(entries)) return []
  const out = []
  for (const entry of entries) {
    if (typeof entry === 'string') {
      if (entry !== '') out.push({ id: entry, name: entry })
    } else if (entry && typeof entry === 'object' && typeof entry.id === 'string' && entry.id !== '') {
      out.push({ id: entry.id, name: typeof entry.name === 'string' && entry.name !== '' ? entry.name : entry.id })
    }
  }
  return out
}

export function modelsOf(provider) {
  return provider?.connected ? choicesOf(provider.models) : []
}

// The efforts a chat can be set to. **Efforts are per model** where the host
// says so — Codex's `models[].efforts`, where one model goes to `ultra` and the
// next stops at `max` — and the row's own `efforts`, which is their union, is
// what a model that names none of its own offers. So the effort list is asked
// about a model, and a model the row does not list is asked about the union.
export function effortsOf(provider, model = null) {
  if (!provider?.connected) return []
  const own = modelEntry(provider, model)?.efforts
  return choicesOf(Array.isArray(own) ? own : provider.efforts)
}

// The provider's own name for a model a turn ran on — "GPT-6-Luna", from the
// row's `models[]` — or `null` when the row does not list that id, which is
// the caller's cue to fall back on the id itself. Read whether or not the
// provider is connected right now: a turn that ran is a fact either way.
export function listedModelName(provider, id) {
  const entry = modelEntry(provider, id)
  return entry && typeof entry.name === 'string' && entry.name !== '' ? entry.name : null
}

// Replies can name a concrete Claude version while the catalog lists aliases.
// Resolve that version for display only; the recorded ID and saved preferences
// never change. Both thread chips and settings use this same presentation.
export function modelLabel(provider, id) {
  if (typeof id !== 'string' || id === '') return { name: null, fullName: null }
  const exact = listedModelName(provider, id)
  let fullName = exact
  if (!fullName || fullName === id) {
    const claude = /^claude-([a-z][a-z0-9-]*?)-(\d+)(?:[-.](\d{1,2}))?(?=$|[-:])/i.exec(id)
    if (claude) {
      const [, alias, major, minor] = claude
      const catalogName = nameOf(choicesOf(provider?.models), alias)
      const family = catalogName && catalogName !== alias
        ? catalogName.replace(/\s+\d+(?:\.\d+)*\b.*$/, '').trim()
        : alias.split('-').map((part) => part[0].toUpperCase() + part.slice(1)).join(' ')
      fullName = `${family} ${major}${minor === undefined ? '' : `.${minor}`}`
    } else {
      const gpt = /^gpt-(\d+(?:\.\d+)*)(?=$|[-:])/i.exec(id)
      const alias = provider?.id === 'claude-code' && choicesOf(provider.models).some((entry) => entry.id === id) && /^[a-z]+$/i.test(id)
      fullName = gpt ? `GPT-${gpt[1]}` : alias ? id[0].toUpperCase() + id.slice(1) : id
    }
  }
  // OpenAI catalog names, including their variants, are already the intended
  // labels. Other vendor prefixes/qualifiers can yield to family + generation.
  const keepCatalog = exact && (provider?.id === 'codex' || provider?.id === 'openai' || /^gpt-/i.test(id))
  const compact = !keepCatalog && fullName.match(/^(?:(?:Claude|Anthropic|OpenAI|Google)\s+)?([\p{L}][\p{L}\p{N}]*[ -]\d+(?:\.\d+)*)(?=$|[\s-])/u)?.[1]
  return { name: compact || (keepCatalog ? fullName : fullName.replace(/^(?:Claude|Anthropic|OpenAI|Google)\s+/u, '')), fullName }
}

export function modelTooltip(provider, id) {
  if (typeof id !== 'string' || id === '') return ''
  const { fullName } = modelLabel(provider, id)
  return [fullName, id && id !== fullName ? id : null].filter(Boolean).join(' · ')
}

// A model's context window as the provider row lists it — `models[].context_window`,
// a positive number of tokens — or `null`. It is the context bar's scale only
// until a turn states its own `context.window`, which always wins; the host
// does not document this field yet, so today it is `null` and the turn's own
// figure is the whole of it.
export function modelWindowOf(provider, id) {
  const n = modelEntry(provider, id)?.context_window
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : null
}

function modelEntry(provider, id) {
  if (typeof id !== 'string' || id === '' || !Array.isArray(provider?.models)) return null
  return provider.models.find((m) => m && typeof m === 'object' && m.id === id) ?? null
}

// Whether an effort is one this model offers — what a change of model is
// checked against, so a chat set to `ultra` that moves to a model stopping at
// `max` goes back to its default rather than asking for something refused.
export function effortFits(provider, model, effort) {
  if (effort === null || effort === undefined) return true
  return effortsOf(provider, model).some((c) => c.id === effort)
}

export function wiredProvider(provider) {
  return Boolean(provider && (provider.connected || provider.sign_in || (provider.login && typeof provider.login === 'object')))
}

// The runtime's wired providers for thread pickers.
// A signed-out provider remains visible but cannot be chosen.
export function startChoices(providers = []) {
  return providers
    .filter(wiredProvider)
    .map((p) => ({
      id: p.id ?? '',
      name: p.name ?? p.id ?? '',
      connected: Boolean(p.connected),
      note: p.connected ? null : 'Sign in on the Providers page',
    }))
}

// What a new chat on this provider runs as unless it is changed: the model and
// the effort the host says it defaults to, `default_model` and `default_effort`
// on the providers route.
//
// **Only a value the provider actually offers counts**, and a provider that
// names no default has none — `null`, which is the agent's own default and is
// the absence of a `model` on the way out, never a name this app invents. So a
// host that has never heard of these two fields is a New-chat sheet reading
// "Alfred's default", which is what it read before there was a preselection at
// all.
export function defaultsOf(provider) {
  const models = modelsOf(provider)
  // The host's default model is `default_model` on the row, or — Codex's shape
  // — the one entry in `models` carrying `default: true`.
  const flagged = Array.isArray(provider?.models) ? provider.models.find((m) => m && typeof m === 'object' && m.default === true)?.id : null
  const model = offered(models, provider?.default_model) ?? offered(models, flagged)
  return {
    model,
    effort: offered(effortsOf(provider, model), provider?.default_effort),
  }
}

function offered(choices, id) {
  if (typeof id !== 'string' || id === '') return null
  return choices.some((c) => c.id === id) ? id : null
}

// What to call the value a record is set to. The host's name for it when the
// route offers one, and the id itself when it does not — a record can name a
// model the list has since stopped offering, and the honest answer then is the
// id the session is actually running on.
export function nameOf(choices, id) {
  if (typeof id !== 'string' || id === '') return null
  return choices.find((c) => c.id === id)?.name ?? id
}

// "1 of 4 connected" — the Settings row's second line. Both numbers are the
// host's: this page never carries a list of providers of its own.
// A provider the host lists but cannot connect yet ("Coming later" on the
// Providers page) is not counted (0.2.1; the 0.2.0 walk read "0 of 5
// connected" with two of the five not connectable).
export function comingLater(provider) {
  return Boolean(provider) && !provider.connected && !wiredProvider(provider) && /not wired|not implemented|coming/i.test(provider.reason || '')
}
export function connectedCount(providers = []) {
  const usable = providers.filter((p) => p && !comingLater(p))
  const total = usable.length
  const connected = usable.filter((p) => p.connected).length
  return { connected, total, label: total === 0 ? 'None known yet' : `${connected} of ${total} connected` }
}

// One row per provider, on the Providers page. The line under the name is the
// host's `reason` when it is not connected — its sentence, shown rather than
// reworded — and what is known of the plan and the quota when it is.
export function providerRows(providers = [], { now = Date.now() } = {}) {
  return providers.filter(Boolean).map((p) => {
    const connected = Boolean(p.connected)
    const windows = windowsOf(p.usage, now)
    const lead = windows.find(w => w.key === 'seven_day') || windows[0]
    const amount = lead?.percent == null ? null : lead.percent > 0 && lead.percent < 1 ? 'Under 1%' : `${Math.round(lead.percent)}%`
    return {
      id: p.id ?? '',
      name: p.name ?? p.id ?? '',
      connected,
      // Not connected: the host's own words. Connected: the plan if the host
      // named one, else the leading window's reading, else that none has
      // arrived — which is not the same as a quota of zero.
      line: connected
        ? [planOf(p), amount ? (lead.key === 'seven_day' ? `${amount} of this week used` : `${amount} of the ${lead.label.toLowerCase()} used`) : null]
            .filter(Boolean)
            .join(' · ') || (p.login?.account ? `Signed in as ${p.login.account}` : 'Connected · no quota reading yet')
        : comingLater(p) ? 'Coming later' : 'Not signed in',
      badge: connected ? 'Connected' : 'Not connected',
    }
  })
}

// One provider's own page.
//
// `plan` is shown when the host names one and is simply absent otherwise: it is
// not a field the session interface documents, so nothing here invents a plan,
// a price or a tier from anything else.
export function providerDetail(provider, { now = Date.now() } = {}) {
  if (!provider) return null
  const connected = Boolean(provider.connected)
  return {
    id: provider.id ?? '',
    name: provider.name ?? provider.id ?? '',
    connected,
    plan: planOf(provider),
    account: text(provider.login?.account),
    // The host's sentence for why it is not wired, shown as given.
    reason: connected ? null : (provider.reason ?? 'Not connected'),
    usageReason: text(provider.usage_reason),
    // A reading, or nothing. Never a zeroed one: `usage: null` and a missing
    // `usage` key both mean no reading has been seen, and a bar at 0% would be
    // a claim this page cannot make.
    usage: usageView(provider.usage, now),
  }
}

// The plan, when the host names one: on the row, or — Codex's shapes — as
// `planType` beside the windows of its reading, or on its `login` block beside
// the account. Codex says `plus`; the page says "Plus".
function planOf(provider) {
  const plan = text(provider?.plan) ?? text(provider?.usage?.planType) ?? text(provider?.login?.plan)
  return plan === null ? null : plan[0].toUpperCase() + plan.slice(1)
}

function text(value) {
  return typeof value === 'string' && value !== '' ? value : null
}

function usageView(usage, now) {
  if (!usage || typeof usage !== 'object') return null
  const windows = windowsOf(usage, now)
  return {
    // When the host last saw this reading. A reading from four hours ago is not
    // a current quota, and saying when it was taken costs one line.
    seenAt: usage.seen_at ?? null,
    seenWord: seenWord(usage.seen_at, now, usage.source),
    status: typeof usage.status === 'string' ? usage.status : null,
    overage: usage.is_using_overage === true,
    windows,
    credits: creditsWord(usage.credits),
  }
}

// Codex's credits, beside its windows: a line only when the account has any.
// `hasCredits` false, or no `credits` at all — Claude Code's reading — is no
// line, not "0 credits".
function creditsWord(credits) {
  if (!credits || typeof credits !== 'object' || credits.hasCredits !== true) return null
  if (credits.unlimited === true) return 'Credits · unlimited'
  const balance = credits.balance
  return typeof balance === 'number' || (typeof balance === 'string' && balance !== '') ? `Credits · ${balance}` : 'Credits available'
}

// Every window in the reading, leading with the two the interface names.
function windowsOf(usage, now = Date.now()) {
  if (!usage || typeof usage !== 'object') return []
  const keys = Object.keys(usage).filter((key) => !NOT_A_WINDOW.has(key) && isWindow(usage[key]))
  keys.sort((a, b) => rank(a) - rank(b))
  const windows = keys.map((key) => windowView(key, usage[key], now))
  for (const [id, bucket] of Object.entries(usage.buckets ?? {})) {
    if (isWindow(bucket)) windows.push({ ...windowView(`bucket:${id}`, bucket, now), label: text(bucket.limit_name) ?? id })
  }
  return windows
}

function rank(key) {
  const i = LEAD.indexOf(key)
  return i === -1 ? LEAD.length : i
}

function isWindow(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  return typeof value.utilization === 'number' || 'resets_at' in value || 'resets_at_epoch' in value
}

export function windowView(key, window, now = Date.now()) {
  const used = typeof window?.utilization === 'number' && Number.isFinite(window.utilization) ? window.utilization : null
  const percentWord = percentWord_(used)
  const resets = resetWord(window, now)
  return {
    key,
    label: windowLabel(key),
    // The fraction as the design's bar wants it: a width, clamped, never a
    // percentage of a percentage.
    percent: used === null ? null : Math.min(100, Math.max(0, used * 100)),
    width: used === null ? '0%' : `${Math.min(100, Math.max(0, used * 100)).toFixed(1)}%`,
    percentWord,
    resetWord: resets,
    // "12% · resets in 4h 02m" — what the card prints beside the window's name.
    phrase: [percentShort(used), resets].filter(Boolean).join(' · ') || 'no reading',
  }
}

// ------------------------------------------------------- the login, and when
//
// The owner's sign-in with a provider does not last forever, and when it runs
// out **every session the host starts stops working** until a person signs in
// again. It is the one thing on this page that only a person can fix, so it is
// said early: the host hands `expires_at` and `days_left` on the wired row and
// this turns them into the one line the chat and the Providers list both show.
//
// Three things it will not do. It computes no expiry of its own — `days_left`
// is the host's arithmetic, whole days rounded down, negative once the login
// has gone. It says nothing at all about a provider that is not wired: that
// row has no sign-in to renew and the host's own sentence is already on it.
// And `expires_at`/`days_left` are `null` for some credentials, which is
// "connected, expiry unknown" and not a warning — a page that printed "expires
// in null days" would be inventing one.
//
// **The warning starts at three days**, which is a little under four days out,
// because early is the right side to be wrong on for something only a person
// can do.
const WARN_DAYS = 3

export function loginView(provider, { now = Date.now() } = {}) {
  if (!provider) return null
  const connected = Boolean(provider.connected)
  const login = provider.login && typeof provider.login === 'object' ? provider.login : null
  if (!login && !connected && !provider.sign_in) return null
  const name = provider.name ?? provider.id ?? 'This provider'
  const days = typeof login?.days_left === 'number' ? login.days_left : null
  const expiresAt = typeof login?.expires_at === 'string' ? login.expires_at : null
  // **Never signed in is not expired.** A provider whose `login.source` is
  // `none` has no credential on the box at all — Codex before anybody has
  // signed in to it — and that is a choice not yet made, not a sign-in that ran
  // out: no warning, no dot, and the button says "Sign in".
  const never = !connected && (!login || login.source === 'none')
  const level = never ? 'none' : !connected || (days !== null && days < 0) ? 'expired' : days !== null && days <= WARN_DAYS ? 'warn' : 'none'
  return {
    id: provider.id ?? '',
    name,
    connected,
    daysLeft: days,
    expiresAt,
    // `none`, `warn` or `expired` — which colour the line takes, and whether
    // there is a line at all.
    level,
    // What the card's button says: a first sign-in, or a renewal.
    action: connected ? 'Sign in again' : 'Sign in',
    // Who is signed in and on what, when the host says — Codex's `account` and
    // `plan`. One line, or `null`; nothing is invented for a host that is silent.
    who: [text(login?.plan), text(login?.account)].filter(Boolean).join(' · ') || null,
    // The notice above a composer and at the top of the Providers list. The
    // words before the link; "Sign in again" is the link and is the page's.
    notice: level === 'none' ? null : level === 'expired' ? `${name} sign-in has expired` : `${name} sign-in expires in ${dayWords(days)}`,
    // The plain line on the provider's own page. Connected with no readable
    // expiry says so rather than saying nothing.
    line: never
      ? 'Not signed in'
      : !connected
      ? `${name} sign-in has expired`
      : expiresAt === null
        ? 'Signed in'
        : days !== null && days < 0
          ? `Sign-in expired ${dayMonth(expiresAt)}`
          : `Signed in · expires ${dayMonth(expiresAt)}`,
  }
}

function dayWords(days) {
  if (days === null) return 'less than a day'
  if (days <= 0) return 'less than a day'
  return days === 1 ? '1 day' : `${days} days`
}

function dayMonth(iso) {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const d = new Date(t)
  return formatDate(d, { day: 'numeric', month: 'short' })
}

// ------------------------------------------------- the ring in the chat head
//
// One figure for a whole subscription, for the ring drawn round the provider's
// mark at the top of a chat. The owner, 2026-09-22: "show a circle version of the
// logo, with a band around it showing the percentage of usage of my
// subscription. The band should get yellow once I am at 70% and red once I am
// at 90%."
//
// **The figure is the highest utilization of every stated window**, including
// buckets: a person is stopped by whichever limit runs out
// first, so a ring showing the lower of them would go quiet on the very reading
// that matters. The provider's own page draws each by name.
//
// `percent` is `null` when no reading has been seen — an empty track, which is
// not a ring at 0%: one says nothing is known, the other says nothing is used.
export function quotaRing(usage) {
  const used = windowsOf(usage).map((w) => w.percent).filter((n) => typeof n === 'number' && Number.isFinite(n))
  if (used.length === 0) return { percent: null, level: 'empty' }
  const percent = Math.max(...used)
  return { percent, level: percent >= 90 ? 'high' : percent >= 70 ? 'warn' : 'calm' }
}

// A reading off the event stream, in the shape the providers route answers.
//
// The agent CLI volunteers its quota mid-turn — `rate_limit_event`, carrying
// `rate_limit_info.unifiedWindows` — so a chat that is open watches its own
// subscription being spent and the ring moves without anything being fetched
// again. It is the same reading the providers route gives, arriving earlier.
//
// The window names are the CLI's, and it has said them more than one way, so
// they are put into the interface's own two names here — once, at the edge —
// and every window the event carries is copied, named as it came, for the same
// reason the providers page reads windows by name: a window this app has not
// heard of is still a window.
const WINDOW_ALIASES = {
  '5h': 'five_hour',
  five_hour: 'five_hour',
  fiveHour: 'five_hour',
  '7d': 'seven_day',
  seven_day: 'seven_day',
  sevenDay: 'seven_day',
}

export function usageFromRateLimit(event, { now = Date.now(), snapshot = null } = {}) {
  const info = event?.rate_limit_info
  const windows = info?.unifiedWindows
  if (!windows || typeof windows !== 'object') return null
  // A native replay need not carry a timestamp. Its opening snapshot's
  // record_seq identifies history, but last_event_at is only an upper bound,
  // not this event's time. Never restamp that history as a fresh reading.
  const replay = typeof event.seq === 'number' && typeof snapshot?.record_seq === 'number' && event.seq <= snapshot.record_seq
  const eventTime = Date.parse(event.ts ?? '')
  if (replay && Number.isNaN(eventTime)) return null
  // What the event did not state it leaves out, rather than stating as `null`
  // or `false`: this reading is merged over the one already held, and a key it
  // never mentioned is no news about that key.
  const usage = {
    seen_at: new Date(Number.isNaN(eventTime) ? now : eventTime).toISOString(),
    source: 'event',
    session_id: event.session_id ?? null,
  }
  if (typeof info.status === 'string') usage.status = info.status
  if (typeof info.is_using_overage === 'boolean') usage.is_using_overage = info.is_using_overage
  let any = false
  for (const [key, raw] of Object.entries(windows)) {
    const window = withResetTimes(raw)
    if (!isWindow(window)) continue
    usage[WINDOW_ALIASES[key] ?? key] = window
    any = true
  }
  return any ? usage : null
}

// Claude Code's windows say when they reset as `resetsAt`, in epoch seconds,
// and nothing else; the interface's own two names are `resets_at_epoch` and an
// ISO `resets_at`, which is what this page reads. Put once, here, at the edge.
function withResetTimes(window) {
  if (!window || typeof window !== 'object' || Array.isArray(window)) return window
  if (typeof window.resetsAt !== 'number' || 'resets_at_epoch' in window || 'resets_at' in window) return window
  const { resetsAt, ...rest } = window
  return { ...rest, resets_at_epoch: resetsAt, resets_at: new Date(resetsAt * 1000).toISOString().replace('.000Z', 'Z') }
}

// A mid-chat reading onto the one the provider row already holds. The event
// carries windows, so the windows it carries replace those windows and every
// other key stays — Codex's `credits` and `planType`, and any window this
// event did not mention, are kept until the next `/providers` reading says
// otherwise. A replayed or repeated reading cannot overwrite a newer read.
export function mergeUsage(held, reading, provider) {
  const base = held && typeof held === 'object' ? held : {}
  const readAt = Date.parse(reading?.seen_at ?? '')
  const heldAt = Date.parse(base.seen_at ?? '')
  if (Number.isNaN(readAt) || (!Number.isNaN(heldAt) && readAt <= heldAt)) return held
  const buckets = reading.buckets ? { buckets: { ...base.buckets, ...reading.buckets } } : {}
  return { ...base, ...reading, ...buckets, provider }
}

export function windowLabel(key) {
  if (WINDOW_LABELS[key]) return WINDOW_LABELS[key]
  if (key.startsWith('seven_day_')) return `Weekly · ${windowLabel(key.slice('seven_day_'.length))}`
  const minutes = /^window_(\d+)_min$/.exec(key)
  if (minutes && Number(minutes[1]) > 0) {
    const n = Number(minutes[1])
    for (const [length, unit] of [[10080, 'week'], [1440, 'day'], [60, 'hour'], [1, 'minute']]) {
      if (n % length === 0) return `${n / length}-${unit} window`
    }
  }
  const words = String(key).replace(/[_-]+/g, ' ').trim()
  return words === '' ? 'Window' : words[0].toUpperCase() + words.slice(1)
}

function percentShort(used) {
  if (used === null) return null
  const pct = used * 100
  if (pct > 0 && pct < 1) return '<1%'
  return `${Math.round(Math.min(100, Math.max(0, pct)))}%`
}

function percentWord_(used) {
  if (used === null) return null
  const pct = used * 100
  if (pct <= 0) return 'none of it used'
  if (pct < 1) return 'under 1% used'
  return `about ${Math.round(pct)}% used`
}

// Both forms of the reset time are on the record, so a page showing "resets at
// midnight" and a page counting down each read the one they want. This one
// wants whichever is nearer plain speech at the distance it is.
function resetWord(window, now) {
  const t = resetAt(window)
  if (t === null) return null
  const dt = t - now
  if (dt <= 0) return 'due to reset'
  // Within a day, a countdown: "resets in 4h 02m". Past that, the day and the
  // clock it resets at: "resets Sun 00:00". Past a week, the date.
  if (dt < 3600000) return `resets in ${Math.max(1, Math.round(dt / 60000))}m`
  if (dt < 86400000) {
    const mins = Math.floor(dt / 60000)
    return `resets in ${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`
  }
  const then = new Date(t)
  if (dt < 7 * 86400000) return `resets ${formatDate(then, { weekday: 'short' })} ${formatTime(then)}`
  return `resets ${formatDate(then, { day: 'numeric', month: 'short' })}`
}

function resetAt(window) {
  if (typeof window?.resets_at_epoch === 'number') return window.resets_at_epoch * 1000
  const t = Date.parse(window?.resets_at ?? '')
  return Number.isNaN(t) ? null : t
}

function seenWord(seenAt, now, source) {
  const t = Date.parse(seenAt ?? '')
  if (Number.isNaN(t)) return null
  const dt = now - t
  const ago = dt < 60000 ? 'just now' : `${longAgo(dt)} ago`
  return source === 'event' ? `seen ${ago}, from a chat` : `read ${ago}`
}
