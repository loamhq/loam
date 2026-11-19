import nodeOs from 'node:os'
import nodeFs from 'node:fs'

// Settings › About Loam's "This server" card (2026-11-18): what the machine
// Loam runs on is, read where the app runs, which is the same machine as the
// runtime. Sizes in bytes; the page words them. Nothing here names a person
// or a path.
export function serverFacts({ os = nodeOs, fs = nodeFs, disk = '/' } = {}) {
  let storage = null
  try {
    const s = fs.statfsSync(disk)
    storage = { total: s.blocks * s.bsize, free: s.bavail * s.bsize }
  } catch { storage = null }
  const cpus = os.cpus()
  return {
    name: os.hostname(),
    system: [os.type(), os.release()].filter(Boolean).join(' '),
    arch: os.arch(),
    processors: cpus.length || null,
    memory: { total: os.totalmem(), free: os.freemem() },
    storage,
    up_seconds: Math.round(os.uptime()),
  }
}

// Settings › Account (2026-11-18): who is signed in, from the headers the
// login's gate sets on every request it lets through (Caddy's forward_auth
// `copy_headers`; the browser's own subnet has them taken off). A request
// that did not come through the gate (the loopback port) has none, and the
// page falls back to the owner's name.
export function signedIn(headers = {}) {
  const one = name => {
    const value = headers[name.toLowerCase()]
    const text = Array.isArray(value) ? value[0] : value
    return typeof text === 'string' && text.trim() ? text.trim().slice(0, 200) : null
  }
  return { username: one('Remote-User'), name: one('Remote-Name'), email: one('Remote-Email') }
}
