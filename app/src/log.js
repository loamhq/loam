// The app's own record of what it did: one JSON object per line, on stdout,
// which is what the container keeps. A line is for counting and for finding —
// how many requests, how many failed, which one — so it carries numbers and
// names and never what a person said or sent: no body, no cookie, no
// authorization header, no token, no key.

import { randomBytes } from 'node:crypto'

// Lowest first. A line below the configured level is not written; `off` writes
// nothing at all.
export const LOG_LEVELS = ['debug', 'info', 'warn', 'error', 'off']

export function logLevel(value) {
  const said = String(value ?? '').trim().toLowerCase()
  return LOG_LEVELS.includes(said) ? said : 'info'
}

export function createLog({ level = 'info', write = (line) => process.stdout.write(line), now = () => new Date() } = {}) {
  const floor = LOG_LEVELS.indexOf(logLevel(level))

  function line(lvl, fields) {
    if (LOG_LEVELS.indexOf(lvl) < floor) return
    write(`${JSON.stringify({ ts: now().toISOString(), level: lvl, ...fields })}\n`)
  }

  return {
    debug: (fields) => line('debug', fields),
    info: (fields) => line('info', fields),
    warn: (fields) => line('warn', fields),
    error: (fields) => line('error', fields),
  }
}

// Short enough to read aloud, long enough not to repeat within a day's log.
export function requestId() {
  return randomBytes(4).toString('hex')
}

// The query parameters worth keeping in a log line, and their values. They are
// the ones the page is allowed to send (see `listQuery` and the thread relay in
// server.js), and none of them is secret. Anything else is named but not
// quoted — a parameter this app does not know about is one it cannot vouch for.
const QUERY_KEPT = new Set(['agent', 'archived', 'from', 'last', 'before'])

export function loggedQuery(searchParams) {
  const parts = []
  for (const [key, value] of searchParams) {
    parts.push(QUERY_KEPT.has(key) ? `${key}=${value}` : `${key}=…`)
  }
  return parts.length === 0 ? null : parts.join('&')
}
