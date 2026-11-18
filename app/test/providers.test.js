import { formatTime, formatDate } from '../public/time.js'
// The Providers screens' model, with no browser anywhere near it.
//
// What is asserted here is the three readings the interface is explicit about
// and a page could plausibly get wrong: that `utilization` is a fraction, that
// no reading is not a reading of zero, and that a window nobody has heard of is
// still drawn.

import test from 'node:test'
import assert from 'node:assert/strict'

import { connectedCount, providerRows, providerDetail, windowLabel, windowView, quotaRing, usageFromRateLimit, mergeUsage, loginView, defaultsOf, effortsOf, effortFits, startChoices, modelWindowOf } from '../public/providers.js'

const NOW = Date.parse('2026-09-18T12:00:00Z')

test('Antigravity signs in through the catalog without inventing quota, plan or expiry', () => {
  const sign_in = { kind: 'paste-code', start: '/v1/providers/antigravity/login', status: '/v1/providers/antigravity/login', cancel: '/v1/providers/antigravity/login', code: '/v1/providers/antigravity/login/code', sign_out: '/v1/providers/antigravity/logout' }
  const provider = { id: 'antigravity', name: 'Antigravity', connected: true, reason: null, usage: null, sign_in, login: { source: 'oauth', account: 'person@example.invalid' } }
  const detail = providerDetail(provider)
  assert.equal(detail.usage, null)
  assert.equal(detail.plan, null)
  assert.equal(providerRows([provider])[0].line, 'Signed in as person@example.invalid')
  assert.equal(loginView(provider).expiresAt, null)
  assert.equal(loginView(provider).notice, null)
  const unsigned = { ...provider, connected: false, reason: 'not signed in', login: { source: 'none' } }
  assert.equal(loginView(unsigned).action, 'Sign in')
  assert.equal(loginView(unsigned).level, 'none')
  assert.deepEqual(startChoices([unsigned]), [{ id: 'antigravity', name: 'Antigravity', connected: false, note: 'Sign in on the Providers page' }])
  assert.equal(providerRows([unsigned])[0].line, 'Not signed in')
  assert.deepEqual(startChoices([provider, { id: 'future', name: 'Future engine', connected: false, sign_in }]).map((p) => p.id), ['antigravity', 'future'])
})

// The answer the host gives, with the wired one and the three that are not.
function providers() {
  return [
    {
      id: 'claude-code',
      name: 'Claude Code',
      connected: true,
      reason: null,
      usage: {
        provider: 'claude-code',
        seen_at: new Date(NOW - 12 * 60000).toISOString(),
        session_id: 's1',
        status: 'allowed',
        is_using_overage: false,
        five_hour: { utilization: 0.06, resets_at: new Date(NOW + 2 * 3600000).toISOString() },
        seven_day: { utilization: 0.24, resets_at: new Date(NOW + 3 * 86400000).toISOString() },
      },
    },
    { id: 'codex', name: 'Codex', connected: false, reason: 'not wired' },
    { id: 'grok', name: 'Grok', connected: false, reason: 'not wired' },
    { id: 'opencode', name: 'OpenCode', connected: false, reason: 'not wired' },
  ]
}

test('the Settings row counts what the host said, and counts nothing of its own', () => {
  assert.equal(connectedCount(providers()).label, '1 of 1 connected', 'the three "not wired" are Coming later, not counted')
  assert.equal(connectedCount([]).label, 'None known yet')
  // The 0.2.0 walk: "0 of 5 connected" counted the two marked "Coming later".
  const fresh = [{ id: 'claude', sign_in: true }, { id: 'codex', sign_in: true }, { id: 'antigravity', login: {} },
    { id: 'gemini', reason: 'Not wired yet' }, { id: 'kimi', reason: 'Coming later' }]
  assert.equal(connectedCount(fresh).label, '0 of 3 connected')
  assert.deepEqual(providerRows(fresh).map(r => r.line), ['Not signed in', 'Not signed in', 'Not signed in', 'Coming later', 'Coming later'])
})

test('a provider that is not wired is a row, and the words on it are the host’s', () => {
  const rows = providerRows(providers(), { now: NOW })
  assert.equal(rows.length, 4, 'every provider has a row: a missing one is a bug report')
  const codex = rows.find((r) => r.id === 'codex')
  assert.equal(codex.connected, false)
  // The host's sentence, shown rather than reworded.
  assert.equal(codex.line, 'Coming later')
})

test('utilization is a fraction: 0.06 is about 6% of the window and a bar 6% wide', () => {
  const w = windowView('five_hour', { utilization: 0.06, resets_at: new Date(NOW + 2 * 3600000).toISOString() }, NOW)
  assert.equal(w.width, '6.0%')
  assert.equal(w.percentWord, 'about 6% used')
  assert.match(w.phrase, /^6% · /)
  // And the other way round: a full window is a full bar, not 100 times one.
  assert.equal(windowView('seven_day', { utilization: 1 }, NOW).width, '100.0%')
})

test('the reset time is a countdown within a day, a day and a clock within a week, a date past it', () => {
  const at = (ms) => windowView('five_hour', { utilization: 0.5, resets_at: new Date(NOW + ms).toISOString() }, NOW).resetWord
  assert.equal(at(25 * 60000), 'resets in 25m')
  assert.equal(at((4 * 60 + 2) * 60000), 'resets in 4h 02m')
  // Read back through the reader's own clock rather than against a time zone
  // this test would have to assume.
  const inThree = new Date(NOW + 3 * 86400000)
  assert.equal(at(3 * 86400000), `resets ${formatDate(inThree, { weekday: 'short' })} ${formatTime(inThree)}`)
  assert.equal(at(20 * 86400000), `resets ${formatDate(NOW + 20 * 86400000, { day: 'numeric', month: 'short' })}`)
})

test('a window this page has never heard of is still drawn, and after the two that lead', () => {
  const detail = providerDetail(
    {
      id: 'claude-code',
      name: 'Claude Code',
      connected: true,
      usage: {
        provider: 'claude-code',
        seen_at: new Date(NOW).toISOString(),
        // Out of order on purpose, and with one the interface never named.
        opus_weekly: { utilization: 0.5, resets_at: new Date(NOW + 86400000).toISOString() },
        seven_day: { utilization: 0.24 },
        five_hour: { utilization: 0.25 },
      },
    },
    { now: NOW },
  )
  assert.deepEqual(
    detail.usage.windows.map((w) => w.key),
    ['five_hour', 'seven_day', 'opus_weekly'],
    'the two named lead, and whatever else the provider reported follows',
  )
  assert.equal(windowLabel('five_hour'), '5-hour window')
  assert.equal(windowLabel('seven_day'), 'Weekly')
  assert.equal(windowLabel('opus_weekly'), 'Opus weekly')
})

test('no reading is not a reading of zero', () => {
  // `usage: null` — connected, but nothing has been volunteered yet.
  const fresh = providerDetail({ id: 'claude-code', name: 'Claude Code', connected: true, usage: null }, { now: NOW })
  assert.equal(fresh.usage, null, 'a page told this draws words, never an empty bar')

  // And one that is not wired carries no `usage` key at all.
  const codex = providerDetail({ id: 'codex', name: 'Codex', connected: false, reason: 'not wired' }, { now: NOW })
  assert.equal(codex.usage, null)
  assert.equal(codex.reason, 'not wired')
  assert.equal(codex.plan, null, 'nothing invents a plan for a provider the host said nothing about')
})

test('the reading says when it was taken, because four hours ago is not a current quota', () => {
  const detail = providerDetail(providers()[0], { now: NOW })
  assert.equal(detail.usage.seenWord, 'read 12 min ago')
  assert.equal(detail.usage.status, 'allowed')
  assert.equal(detail.usage.overage, false)
  // What the card prints beside the window's name: "6% · resets …".
  assert.match(detail.usage.windows[0].phrase, /^6% · resets /)
})

// ------------------------------------------------- the ring in the chat head

const at = (five, seven) => ({ five_hour: { utilization: five }, seven_day: { utilization: seven } })

test('the ring is the higher of the two windows, as a percentage', () => {
  // A fraction, and the higher one — whichever window it is in. A ring showing
  // the lower of the two would go quiet on the very reading that stops a
  // person working.
  assert.equal(quotaRing(at(0.25, 0.24)).percent, 25)
  assert.equal(quotaRing(at(0.1, 0.62)).percent, 62)
  // Only one window reported is that window.
  assert.equal(quotaRing({ seven_day: { utilization: 0.4 } }).percent, 40)
  // Model limits and other named windows can stop work too.
  assert.deepEqual(quotaRing({ opus_weekly: { utilization: 0.99 } }), { percent: 99, level: 'high' })
})

test('the three bands are where the owner said they are', () => {
  // "The band should get yellow once I am at 70% and red once I am at 90%" —
  // once, so 70 is amber and 69 is not, 90 is red and 89 is not.
  assert.deepEqual(quotaRing(at(0, 0)), { percent: 0, level: 'calm' })
  assert.deepEqual(quotaRing(at(0.69, 0)), { percent: 69, level: 'calm' })
  assert.equal(quotaRing(at(0.7, 0)).level, 'warn')
  assert.equal(quotaRing(at(0.899, 0)).level, 'warn')
  assert.equal(quotaRing(at(0.9, 0)).level, 'high')
  assert.equal(quotaRing(at(1.4, 0)).percent, 100, 'a window past its limit is a full ring, never more')
})

test('no reading is an empty track, and that is not a ring at zero', () => {
  // The two are different facts and the page draws them differently: 0% says
  // nothing has been used, `null` says nothing is known.
  assert.deepEqual(quotaRing(null), { percent: null, level: 'empty' })
  assert.deepEqual(quotaRing(undefined), { percent: null, level: 'empty' })
  assert.deepEqual(quotaRing({ status: 'allowed' }), { percent: null, level: 'empty' })
  assert.deepEqual(quotaRing({ seven_day: { utilization: Infinity }, buckets: { invalid: { utilization: NaN } } }), { percent: null, level: 'empty' })
  assert.equal(quotaRing(at(0, 0)).percent, 0)
})

test('a rate_limit_event is the same reading, arriving earlier', () => {
  const event = {
    type: 'rate_limit_event',
    session_id: 'live-1',
    seq: 9,
    rate_limit_info: {
      status: 'allowed',
      unifiedWindows: {
        five_hour: { utilization: 0.72, resets_at: '2026-09-18T15:00:00Z' },
        seven_day: { utilization: 0.31 },
        opus_weekly: { utilization: 0.12 },
      },
    },
  }
  const usage = usageFromRateLimit(event, { now: NOW })
  assert.equal(usage.session_id, 'live-1')
  assert.equal(usage.status, 'allowed')
  assert.equal(usage.seen_at, '2026-09-18T12:00:00.000Z')
  // Every window it carried, named as it came — the same rule the provider's
  // page reads windows by.
  assert.deepEqual(Object.keys(usage).filter((k) => k.includes('_') && k !== 'seen_at' && k !== 'session_id' && k !== 'is_using_overage'), [
    'five_hour',
    'seven_day',
    'opus_weekly',
  ])
  assert.deepEqual(quotaRing(usage), { percent: 72, level: 'warn' })

  // The CLI has said the two window names more than one way, and they land as
  // the interface's own names, once, here.
  const short = usageFromRateLimit({ rate_limit_info: { unifiedWindows: { '5h': { utilization: 0.95 }, '7d': { utilization: 0.2 } } } })
  assert.equal(quotaRing(short).level, 'high')

  // Nothing to read is nothing: an event of another shape moves no ring.
  assert.equal(usageFromRateLimit({ type: 'rate_limit_event' }), null)
  assert.equal(usageFromRateLimit({ rate_limit_info: { unifiedWindows: {} } }), null)
})

test('a mid-chat event merges into a Codex reading: its windows replace, credits and planType stay', () => {
  const held = {
    provider: 'codex',
    seen_at: '2026-09-18T11:00:00.000Z',
    status: 'allowed',
    five_hour: { utilization: 0.1, resets_at_epoch: 1789700000, resets_at: '2026-09-18T15:33:20Z' },
    seven_day: { utilization: 0.2, resets_at_epoch: 1790000000, resets_at: '2026-09-22T02:53:20Z' },
    credits: { hasCredits: true, unlimited: false, balance: '120' },
    planType: 'plus',
  }
  // Windows only, as the runtime sends them on the stream.
  const event = {
    type: 'rate_limit_event',
    session_id: 'codex-1',
    rate_limit_info: { unifiedWindows: { five_hour: { utilization: 0.6, resets_at_epoch: 1789710000, resets_at: '2026-09-18T18:20:00Z' } } },
  }
  const merged = mergeUsage(held, usageFromRateLimit(event, { now: NOW }), 'codex')
  assert.deepEqual(merged, {
    provider: 'codex',
    seen_at: '2026-09-18T12:00:00.000Z',
    source: 'event',
    session_id: 'codex-1',
    status: 'allowed',
    five_hour: { utilization: 0.6, resets_at_epoch: 1789710000, resets_at: '2026-09-18T18:20:00Z' },
    seven_day: { utilization: 0.2, resets_at_epoch: 1790000000, resets_at: '2026-09-22T02:53:20Z' },
    credits: { hasCredits: true, unlimited: false, balance: '120' },
    planType: 'plus',
  })
  const detail = providerDetail(codexRow(merged), { now: NOW })
  assert.equal(detail.plan, 'Plus')
  assert.equal(detail.usage.credits, 'Credits · 120')
  // And nothing held is a reading too.
  assert.equal(mergeUsage(null, usageFromRateLimit(event, { now: NOW }), 'codex').five_hour.utilization, 0.6)
})

test('a window from a Claude event keeps its reset time: resetsAt becomes resets_at_epoch and resets_at', () => {
  // Claude Code's CLI says only `resetsAt`, in epoch seconds.
  const event = {
    type: 'rate_limit_event',
    session_id: 'live-1',
    rate_limit_info: {
      status: 'allowed',
      unifiedWindows: { five_hour: { utilization: 0.25, resetsAt: 1789754400 }, seven_day: { utilization: 0.24, resetsAt: 1790186400 } },
    },
  }
  const usage = usageFromRateLimit(event, { now: NOW })
  assert.deepEqual(usage.five_hour, { utilization: 0.25, resets_at_epoch: 1789754400, resets_at: '2026-09-18T18:00:00Z' })
  assert.deepEqual(usage.seven_day, { utilization: 0.24, resets_at_epoch: 1790186400, resets_at: '2026-09-23T18:00:00Z' })
  // So the Providers row and the ring's sheet still say when it resets.
  const view = windowView('five_hour', usage.five_hour, NOW)
  assert.match(JSON.stringify(view), /resets in 6h 00m/)
})

test('only a strictly newer event moves named windows; older and equal readings cannot resurrect a missing window', () => {
  const held = { seen_at: new Date(NOW).toISOString(), source: 'read', seven_day: { utilization: 0.05 }, planType: 'prolite' }
  for (const when of [NOW - 60000, NOW]) {
    const reading = usageFromRateLimit({ ts: new Date(when).toISOString(), rate_limit_info: { unifiedWindows: { five_hour: { utilization: 0.8 } } } })
    assert.equal(mergeUsage(held, reading, 'codex'), held)
  }
  const reading = usageFromRateLimit({ ts: new Date(NOW + 60000).toISOString(), rate_limit_info: { unifiedWindows: { seven_day: { utilization: 0.06 } } } })
  const merged = mergeUsage(held, reading, 'codex')
  assert.equal(merged.seven_day.utilization, 0.06)
  assert.equal(merged.planType, 'prolite')
  assert.equal(merged.five_hour, undefined)
  assert.equal(merged.source, 'event')
})

test('timestamp-less replay is not a fresh reading, even if its snapshot is newer than the Providers read', () => {
  const event = { seq: 4, rate_limit_info: { unifiedWindows: { five_hour: { utilization: 0.8 } } } }
  assert.equal(usageFromRateLimit(event, { now: NOW, snapshot: { record_seq: 5, last_event_at: new Date(NOW).toISOString() } }), null)
  assert.equal(usageFromRateLimit(event, { now: NOW, snapshot: { record_seq: 3 } }).seen_at, new Date(NOW).toISOString())
})

test('model weekly limits, windows by length, and buckets are named and all contribute to the ring', () => {
  const usage = {
    seven_day: { utilization: 0.05 },
    seven_day_fable: { utilization: 0.3 },
    seven_day_overage_included: { utilization: 0.4 },
    window_120_min: { utilization: 0.5 },
    window_90_min: { utilization: 0.6 },
    buckets: { review: { utilization: 0.91, limit_name: 'Code review' }, extra: { utilization: 0.2 } },
  }
  const detail = providerDetail(codexRow(usage), { now: NOW })
  assert.deepEqual(detail.usage.windows.map((w) => w.label), ['Weekly', 'Weekly · Fable', 'Weekly · Overage included', '2-hour window', '90-minute window', 'Code review', 'extra'])
  assert.deepEqual(quotaRing(usage), { percent: 91, level: 'high' })
})

// ------------------------------------------------------ the sign-in, and when
//
// When a login runs out **every session the host starts stops working**, and
// nothing on this page can fix it. The owner, 2026-09-22: "I should also have a
// notification telling me when I am 3 days away from needing to log in again."
// The arithmetic is the host's — `days_left` is whole days rounded down and
// goes negative once the login has gone — and what is asserted here is the
// threshold and the words.

const wired = (login) => ({ id: 'claude-code', name: 'Claude Code', connected: true, reason: null, login })

test('the warning starts at three days and counts down in the host’s own number', () => {
  assert.equal(loginView(wired({ days_left: 4, expires_at: '2026-09-27T09:07:49Z' })).level, 'none')
  assert.equal(loginView(wired({ days_left: 3, expires_at: '2026-09-26T09:07:49Z' })).level, 'warn')
  assert.equal(loginView(wired({ days_left: 3, expires_at: '2026-09-26T09:07:49Z' })).notice, 'Claude Code sign-in expires in 3 days')
  assert.equal(loginView(wired({ days_left: 1, expires_at: '2026-09-24T09:07:49Z' })).notice, 'Claude Code sign-in expires in 1 day')
  // `0` means less than a day remains, which is not "expires in 0 days".
  assert.equal(loginView(wired({ days_left: 0, expires_at: '2026-09-23T09:07:49Z' })).notice, 'Claude Code sign-in expires in less than a day')
})

test('a login that has gone is the error colour, and so is a row that is not connected', () => {
  const gone = loginView(wired({ days_left: -2, expires_at: '2026-09-21T09:07:49Z' }))
  assert.equal(gone.level, 'expired')
  assert.equal(gone.notice, 'Claude Code sign-in has expired')
  assert.equal(gone.line, `Sign-in expired ${formatDate('2026-09-21T09:07:49Z', { day: 'numeric', month: 'short' })}`)

  const broken = loginView({ id: 'claude-code', name: 'Claude Code', connected: false, reason: 'the login was refused', login: { days_left: null, expires_at: null } })
  assert.equal(broken.level, 'expired')
})

test('a provider that is not wired has no sign-in to renew and says nothing about one', () => {
  assert.equal(loginView({ id: 'codex', name: 'Codex', connected: false, reason: 'not wired' }), null)
  assert.equal(loginView(null), null)
})

test('connected with no readable expiry is an answer, not a warning', () => {
  // Not every credential carries an expiry the host can read, and a page that
  // printed "expires in null days" would be inventing one.
  const view = loginView(wired({ days_left: null, expires_at: null, source: 'api-key' }))
  assert.equal(view.level, 'none')
  assert.equal(view.notice, null)
  assert.equal(view.line, 'Signed in')
})

test('the plain line on the provider’s own page names the day it runs out', () => {
  assert.equal(loginView(wired({ days_left: 1, expires_at: '2026-09-24T09:07:49Z' })).line, `Signed in · expires ${formatDate('2026-09-24T09:07:49Z', { day: 'numeric', month: 'short' })}`)
})

// ------------------------------------------------------------------ Codex
//
// The second provider, in the shape the host's runtime quoted (2026-10-01):
// efforts per model, the default model flagged on its entry, and a `login`
// block with no expiry, an account and a plan.

const CODEX = {
  id: 'codex',
  name: 'Codex',
  connected: true,
  reason: null,
  models: [
    { id: 'gpt-6.1-sol', name: 'GPT-6.1-Sol', default: true, efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
    { id: 'gpt-6-luna', name: 'GPT-6-Luna', default: false, efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
  ],
  efforts: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
  usage: null,
  login: { expires_at: null, days_left: null, source: 'chatgpt', account: 'person@example.invalid', plan: 'Pro', status: 'Logged in' },
}

test('Codex: the default model is the one flagged, and efforts are the model’s own', () => {
  assert.deepEqual(defaultsOf(CODEX), { model: 'gpt-6.1-sol', effort: null })
  assert.deepEqual(effortsOf(CODEX, 'gpt-6-luna').map((c) => c.id), ['low', 'medium', 'high', 'xhigh', 'max'])
  // No model, or one the row does not list: the union.
  assert.equal(effortsOf(CODEX).length, 6)
  assert.equal(effortsOf(CODEX, 'gpt-unknown').length, 6)
  assert.equal(effortFits(CODEX, 'gpt-6-luna', 'ultra'), false)
  assert.equal(effortFits(CODEX, 'gpt-6.1-sol', 'ultra'), true)
  assert.equal(effortFits(CODEX, 'gpt-6-luna', null), true)
})

test('the New-chat choices are the wired providers, and one nobody signed in to cannot be chosen', () => {
  const out = { ...CODEX, connected: false, reason: 'no login on the box', login: { ...CODEX.login, source: 'none', account: null, plan: null } }
  const choices = startChoices([{ id: 'claude-code', name: 'Claude Code', connected: true }, out, { id: 'grok', name: 'Grok', connected: false, reason: 'not wired' }])
  assert.deepEqual(choices, [
    { id: 'claude-code', name: 'Claude Code', connected: true, note: null },
    { id: 'codex', name: 'Codex', connected: false, note: 'Sign in on the Providers page' },
  ])
})

test('Codex signed in has no expiry and no warning; never signed in is not expired', () => {
  const view = loginView(CODEX)
  assert.equal(view.line, 'Signed in')
  assert.equal(view.level, 'none')
  assert.equal(view.who, 'Pro · person@example.invalid')
  assert.equal(view.action, 'Sign in again')
  const never = loginView({ ...CODEX, connected: false, login: { expires_at: null, days_left: null, source: 'none', account: null, plan: null } })
  assert.equal(never.line, 'Not signed in')
  assert.equal(never.level, 'none')
  assert.equal(never.notice, null)
  assert.equal(never.who, null)
  assert.equal(never.action, 'Sign in')
  assert.equal(providerDetail(CODEX).plan, 'Pro')
})

// ------------------------------------------------- Codex's reading

// The same shape as Claude Code's, with `credits` and `planType` beside the
// windows — the shape the host's runtime serves on the `codex` row.
const codexUsage = (credits) => ({
  provider: 'codex',
  seen_at: new Date(NOW).toISOString(),
  five_hour: { utilization: 0.12, resets_at: new Date(NOW + 3600000).toISOString() },
  seven_day: { utilization: 0.31, resets_at: new Date(NOW + 4 * 86400000).toISOString() },
  credits,
  planType: 'plus',
})
const codexRow = (usage, login = {}) => ({ id: 'codex', name: 'Codex', connected: true, reason: null, usage, login: { source: 'chatgpt', plan: null, ...login } })

test('Codex’s credits and plan sit beside its windows and are neither of them a window', () => {
  const detail = providerDetail(codexRow(codexUsage({ hasCredits: false, unlimited: false, balance: null })), { now: NOW })
  assert.deepEqual(detail.usage.windows.map((w) => w.key), ['five_hour', 'seven_day'])
  assert.deepEqual(detail.usage.windows.map((w) => w.label), ['5-hour window', 'Weekly'])
  assert.equal(detail.plan, 'Plus', 'the plan is planType, capitalised')
  assert.equal(detail.usage.credits, null, 'an account with no credits drew a credits line')
  assert.equal(quotaRing(codexRow(codexUsage(null)).usage).percent, 31)
})

test('credits are a line only when the account has some', () => {
  const credits = (c) => providerDetail(codexRow(codexUsage(c)), { now: NOW }).usage.credits
  assert.equal(credits(undefined), null)
  assert.equal(credits({ hasCredits: true, unlimited: false, balance: '120' }), 'Credits · 120')
  assert.equal(credits({ hasCredits: true, unlimited: true, balance: null }), 'Credits · unlimited')
  assert.equal(credits({ hasCredits: true, unlimited: false, balance: null }), 'Credits available')
})

test('the plan comes from planType or the login’s plan, capitalised, and the list row leads with it', () => {
  assert.equal(providerDetail(codexRow(null, { plan: 'pro' }), { now: NOW }).plan, 'Pro')
  assert.equal(providerDetail(codexRow(null), { now: NOW }).plan, null)
  const [row] = providerRows([codexRow(codexUsage(null))], { now: NOW })
  assert.equal(row.line, 'Plus · 31% of this week used')
  const [bare] = providerRows([codexRow(null)], { now: NOW })
  assert.equal(bare.line, 'Connected · no quota reading yet')
})

// The context bar's scale when a turn states no window of its own: the row's
// `models[].context_window`, a positive number, or nothing.
test('a model’s context window is the row’s figure, or null', () => {
  const row = { ...CODEX, models: [{ id: 'big', name: 'Big', context_window: 1000000 }, { id: 'odd', name: 'Odd', context_window: 0 }, { id: 'plain', name: 'Plain' }] }
  assert.equal(modelWindowOf(row, 'big'), 1000000)
  assert.equal(modelWindowOf(row, 'odd'), null)
  assert.equal(modelWindowOf(row, 'plain'), null)
  assert.equal(modelWindowOf(row, 'missing'), null)
  assert.equal(modelWindowOf(null, 'big'), null)
})
