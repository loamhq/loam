import { installation } from './installation.js'
// Device preferences for the sidebar and Files.
// Thread drafts live in the thread controller; read marks belong to the host.
// Legacy positional keys are discarded by forgetOld(). Old draft content is
// left untouched, with no remaining classic-session UI or storage accessors.


const SIDEBAR = 'loam-ui.sidebar'
const FILES = 'loam-ui.files'
const FILE_PANEL = 'loam-ui.file-panel'
const READS = 'loam-ui.reads' // what this browser used to decide unread by, and no longer does
const POSITIONS = 'loam-ui.seq.' // what an earlier version wrote, and no longer does
const CURRENT = 'loam-ui.session' // which chat was open, and no longer is (2026-10-05)

export function createStore(storage = globalThis.localStorage) {
  // A browser carrying one of the keys this app has stopped writing would
  // otherwise carry it forever. Drop them on the way past.
  forgetOld(storage)

  function read(key, fallback) {
    try {
      const raw = storage?.getItem(key)
      return raw === null || raw === undefined ? fallback : JSON.parse(raw)
    } catch {
      return fallback
    }
  }

  function write(key, value) {
    if (!storage) return
    try {
      if (value === null || value === undefined) storage.removeItem(key)
      else storage.setItem(key, JSON.stringify(value))
    } catch {
      // A browser that refuses storage remembers nothing, which is a smaller
      // problem than a page that will not draw.
    }
  }

  return {
    // Whether the desktop layout's list column is hidden. It is this device's,
    // unlike a pin: hiding the column is how one screen is
    // being looked at, not something true of the sessions.
    get sidebarHidden() {
      return read(SIDEBAR, null) === 'hidden'
    },
    set sidebarHidden(value) {
      write(SIDEBAR, value ? 'hidden' : null)
    },
    // **The Files home's shortcuts, and the two folders its actions write
    // into** (2026-10-07). These are the person's and not the device's, so by
    // the rule above they belong on the record — but the host has no route for
    // a person's preferences yet, and this app keeps no durable state of its
    // own. Until it has one they are this device's, read whole and cleaned,
    // and this getter and setter are the one place that would move.
    get files() {
      return filesSettings(read(FILES, null))
    },
    set files(value) {
      write(FILES, filesSettings(value))
    },
    // Whether a non-Markdown file's companion panel (Comments) stays open or
    // closed on this device across files and reloads: null means automatic
    // (closed when empty, open when unresolved notes exist).
    get filePanel() {
      const value = read(FILE_PANEL, null)
      return value === 'open' || value === 'closed' ? value : null
    },
    set filePanel(value) {
      write(FILE_PANEL, value === 'open' || value === 'closed' ? value : null)
    },
  }
}

// Installation defaults apply only where no personal folder choice is held.
export const FILES_DEFAULTS = Object.freeze({ shortcuts: [], inbox: installation().inbox })

// A folder path as Settings takes one: segments, no `.`/`..`, no slashes at
// either end. Anything else is the default.
function folderSetting(v, fallback) {
  if (typeof v !== 'string') return fallback
  const parts = v.split('/').map((s) => s.trim()).filter((s) => s !== '')
  if (parts.length === 0 || parts.some((s) => s === '.' || s === '..' || /[\\\u0000-\u001f]/.test(s))) return fallback
  return parts.join('/')
}

export function filesSettings(held) {
  const value = held && typeof held === 'object' ? held : {}
  const seen = new Set()
  const shortcuts = (Array.isArray(value.shortcuts) ? value.shortcuts : [])
    .filter((s) => s && typeof s.root === 'string' && s.root !== '' && typeof s.path === 'string')
    .map((s) => s.kind === 'file' ? { root: s.root, path: s.path, kind: 'file' } : { root: s.root, path: s.path })
    .filter((s) => {
      const k = `${s.root}:${s.path}`
      if (seen.has(k)) return false
      seen.add(k)
      return true
    })
  const hiddenShortcuts = (Array.isArray(value.hiddenShortcuts) ? value.hiddenShortcuts : [])
    .filter(s => s && typeof s.root === 'string' && s.root !== '' && typeof s.path === 'string')
    .map(({ root, path }) => ({ root, path }))
  return { shortcuts, hiddenShortcuts, inbox: folderSetting(value.inbox, FILES_DEFAULTS.inbox) }
}

const sameShortcut = (a, b) => a.root === b.root && a.path === b.path

// Host suggestions and personal shortcuts have one membership rule. A removed
// suggestion stays dismissed on reload; adding it again clears the dismissal.
export function fileShortcuts(held, offered = []) {
  const { shortcuts, hiddenShortcuts } = filesSettings(held)
  return [...shortcuts, ...offered.flatMap(({ label, resource }) =>
    shortcuts.some(s => sameShortcut(s, resource)) || hiddenShortcuts.some(s => sameShortcut(s, resource))
      ? [] : [{ root: resource.root, path: resource.path, kind: resource.kind, label, offered: true }])]
}

export function toggleFileShortcut(held, offered, root, path, kind = 'folder') {
  const now = filesSettings(held), target = { root, path }
  const on = fileShortcuts(now, offered).some(s => sameShortcut(s, target))
  const suggested = offered.some(s => sameShortcut(s.resource, target))
  return { ...now,
    shortcuts: on ? now.shortcuts.filter(s => !sameShortcut(s, target)) : [...now.shortcuts, kind === 'file' ? { ...target, kind } : target],
    hiddenShortcuts: [...now.hiddenShortcuts.filter(s => !sameShortcut(s, target)), ...(on && suggested ? [target] : [])],
  }
}

// 400k by default; 100k to 1M in steps of 50k, which is the stepper's own.
function forgetOld(storage) {
  if (!storage) return
  try {
    // Appearance now belongs to the host, so an old browser choice cannot
    // override a persona's edit or the value used by another browser.
    const stale = [READS, CURRENT, 'loam-ui.theme']
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i)
      if (typeof key === 'string' && key.startsWith(POSITIONS)) stale.push(key)
    }
    for (const key of stale) storage.removeItem(key)
  } catch {
    // A browser that refuses storage altogether is one that remembers nothing,
    // which is a smaller problem than not starting.
  }
}
